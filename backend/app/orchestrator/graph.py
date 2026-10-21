"""The stage graph.

    START ─▶ (route by requested_stage) ─▶ <stage agent> ─▶ human_review ─▶ END
                                                              ▲ interrupt(): pauses here

One invocation runs one stage and pauses for review; the paused run is resumed with
`Command(resume=decision)`. Stage agents are stubs until their steps are built (3.x–9.x).
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.db.models.enums import StageName, StageStatus
from app.orchestrator.state import ResearchState, StageOutput

REVIEW_NODE = "human_review"

StageAgent = Callable[[ResearchState], Awaitable[dict[str, Any]]]

STAGE_LABELS: dict[StageName, str] = {
    StageName.IDEA: "Idea & Problem",
    StageName.LITERATURE: "Literature Survey",
    StageName.METHODOLOGY: "Methodology",
    StageName.RESULTS: "Results & Discussion",
    StageName.INTRODUCTION: "Introduction",
    StageName.CONCLUSION: "Conclusion",
    StageName.REFERENCES: "References",
    StageName.REVIEW: "Review",
    StageName.FORMATTING: "Formatting & Export",
}

# Build step that replaces each stub with the real agent
PLANNED_STEP: dict[StageName, str] = {
    StageName.IDEA: "3.1",
    StageName.LITERATURE: "4.1–4.10",
    StageName.METHODOLOGY: "5.1–5.3",
    StageName.RESULTS: "6.1–6.3",
    StageName.INTRODUCTION: "7.1",
    StageName.CONCLUSION: "7.3",
    StageName.REFERENCES: "8.1–8.2",
    StageName.REVIEW: "9.1",
    StageName.FORMATTING: "9.2–9.3",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_stub_agent(stage: StageName) -> StageAgent:
    async def stub(state: ResearchState) -> dict[str, Any]:
        label = STAGE_LABELS[stage]
        lines = [
            f"[Placeholder draft — {label}]",
            f"This text stands in for the {label} agent, which is built in Step {PLANNED_STEP[stage]}.",
        ]
        raw_idea = (state.get("idea") or {}).get("raw_idea")
        if stage == StageName.IDEA and raw_idea:
            lines.append(f"Research idea on file: {raw_idea}")
        if state.get("pending_feedback"):
            lines.append(f"Feedback to address: {state['pending_feedback']}")
        output = StageOutput(draft="\n".join(lines), agent="stub", generated_at=now_iso())
        return {"stage_outputs": {stage.value: output}}

    return stub


STUB_AGENTS: dict[StageName, StageAgent] = {stage: make_stub_agent(stage) for stage in StageName}


async def human_review(state: ResearchState) -> dict[str, Any]:
    """Pause for the researcher (A4.4: nothing is final without approval).

    The resume value is the decision: {"action": "approve" | "reject", "comment"?, "by"?}.
    Step 2.2 adds edit / regenerate / go back.
    """
    stage = state["requested_stage"]
    output = state.get("stage_outputs", {}).get(stage)
    decision: dict[str, Any] = interrupt({"stage": stage, "draft": output["draft"] if output else None})
    action = decision.get("action")
    if action not in ("approve", "reject"):
        raise ValueError(f"Unsupported review action: {action!r}")
    status = StageStatus.APPROVED if action == "approve" else StageStatus.REJECTED
    comment = decision.get("comment")
    return {
        "stage_status": {stage: status.value},
        "approvals": {stage: {"action": action, "at": now_iso(), "by": decision.get("by"), "comment": comment}},
        "pending_feedback": comment if action == "reject" else None,
    }


def _mark_awaiting_review(agent: StageAgent, stage: StageName) -> StageAgent:
    """Wrap an agent so every stage records its status and becomes the project's current stage."""

    async def node(state: ResearchState) -> dict[str, Any]:
        update = await agent(state)
        status = {**update.get("stage_status", {}), stage.value: StageStatus.AWAITING_REVIEW.value}
        return {**update, "stage_status": status, "current_stage": stage.value}

    return node


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None,
    agents: dict[StageName, StageAgent] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    agents = {**STUB_AGENTS, **(agents or {})}
    graph = StateGraph(ResearchState)
    for stage in StageName:
        graph.add_node(stage.value, _mark_awaiting_review(agents[stage], stage))  # type: ignore[call-overload]
        graph.add_edge(stage.value, REVIEW_NODE)
    graph.add_node(REVIEW_NODE, human_review)
    graph.add_conditional_edges(START, lambda s: s["requested_stage"], {s.value: s.value for s in StageName})
    graph.add_edge(REVIEW_NODE, END)
    return graph.compile(checkpointer=checkpointer)
