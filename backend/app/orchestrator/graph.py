"""The stage graph.

    START ─▶ (route by requested_stage) ─▶ <stage agent> ─▶ human_review ─▶ END
                                                              ▲ interrupt(): pauses here

One invocation runs one stage and pauses for review; the paused run is resumed with
`Command(resume=decision)` (approve / reject / edit / regenerate). "Go back" starts a run with
`reopen=True`, which goes straight to review. Stage agents are stubs until their steps are built (3.x–9.x).
"""

from collections.abc import Awaitable, Callable, Hashable
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

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


REVIEW_ACTIONS = ("approve", "reject", "edit", "regenerate")


def _status_update(state: ResearchState, changes: dict[str, str]) -> dict[str, Any]:
    """A stage_status update plus the matching B12 `stale_stages` list."""
    merged = {**state.get("stage_status", {}), **changes}
    stale = [s.value for s in StageName if merged.get(s.value) == StageStatus.STALE.value]
    return {"stage_status": changes, "stale_stages": stale}


async def human_review(state: ResearchState) -> Command[str]:
    """Pause for the researcher (A4.4: nothing is final without approval).

    Resume with a decision {"action": ..., "by"?, "comment"?, "content"?}:
      approve     → stage approved, run ends
      reject      → stage rejected, comment kept as feedback for the next run, run ends
      edit        → `content` replaces the draft, back to review
      regenerate  → the stage's agent runs again with the comment as feedback, then back to review
    """
    stage = state["requested_stage"]
    output = state.get("stage_outputs", {}).get(stage)
    decision: dict[str, Any] = interrupt({"stage": stage, "draft": output["draft"] if output else None})
    action, by, comment = decision.get("action"), decision.get("by"), decision.get("comment")

    if action in ("approve", "reject"):
        status = StageStatus.APPROVED if action == "approve" else StageStatus.REJECTED
        record = {"action": action, "at": now_iso(), "by": by, "comment": comment}
        update = {
            **_status_update(state, {stage: status.value}),
            "approvals": {stage: record},
            "pending_feedback": comment if action == "reject" else None,
            "reopen": False,
        }
        return Command(update=update, goto=END)
    if action == "edit":
        content = decision.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Edit needs non-empty content")
        edited = StageOutput(draft=content, agent="user", generated_at=now_iso())
        return Command(update={"stage_outputs": {stage: edited}, "reopen": False}, goto=REVIEW_NODE)
    if action == "regenerate":
        update = {
            **_status_update(state, {stage: StageStatus.RUNNING.value}),
            "pending_feedback": comment or state.get("pending_feedback"),
            "reopen": False,
        }
        return Command(update=update, goto=stage)
    raise ValueError(f"Unsupported review action: {action!r}")


def _mark_awaiting_review(agent: StageAgent, stage: StageName) -> StageAgent:
    """Wrap an agent so every stage records its status and becomes the project's current stage."""

    async def node(state: ResearchState) -> dict[str, Any]:
        update = await agent(state)
        statuses = {**update.get("stage_status", {}), stage.value: StageStatus.AWAITING_REVIEW.value}
        return {**update, **_status_update(state, statuses), "current_stage": stage.value}

    return node


def _route_from_start(state: ResearchState) -> str:
    # "Go back" reopens a stage's review without re-running its agent
    return REVIEW_NODE if state.get("reopen") else state["requested_stage"]


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None,
    agents: dict[StageName, StageAgent] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    agents = {**STUB_AGENTS, **(agents or {})}
    graph = StateGraph(ResearchState)
    for stage in StageName:
        graph.add_node(stage.value, _mark_awaiting_review(agents[stage], stage))  # type: ignore[call-overload]
        graph.add_edge(stage.value, REVIEW_NODE)
    # The review node routes itself (Command.goto): back to an agent, to itself, or to END
    graph.add_node(REVIEW_NODE, human_review, destinations=(*[s.value for s in StageName], REVIEW_NODE, END))
    routes: dict[Hashable, str] = {REVIEW_NODE: REVIEW_NODE}
    routes.update({s.value: s.value for s in StageName})
    graph.add_conditional_edges(START, _route_from_start, routes)
    return graph.compile(checkpointer=checkpointer)
