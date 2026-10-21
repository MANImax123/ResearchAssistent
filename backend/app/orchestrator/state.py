"""ResearchState (B12): the shared context every stage agent reads and writes.

It lives in the LangGraph checkpointer (thread = project id) and is mirrored to the
`research_state` table after every run. Values must stay JSON-serialisable.
"""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from app.db.models.enums import StageName


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer: a node returning {"stage_status": {"idea": "approved"}} updates one key, not the whole dict."""
    return {**(left or {}), **(right or {})}


class IdeaBlock(TypedDict, total=False):
    raw_idea: str | None
    refined_idea: str | None
    domain: str | None
    keywords: list[str]


class ProblemBlock(TypedDict, total=False):
    statement: str | None
    motivation: str | None
    scope: str | None
    contributions: list[str]


class Objective(TypedDict):
    id: str
    text: str


class ResearchQuestion(TypedDict):
    id: str
    text: str
    linked_objective_ids: list[str]


class MethodologyBlock(TypedDict, total=False):
    architecture: str | None
    algorithms: list[str]
    datasets: list[dict[str, Any]]
    setup: str | None
    metrics: list[str]
    diagrams: list[str]


class ResultsBlock(TypedDict, total=False):
    metrics: list[dict[str, Any]]
    tables: list[str]
    figures: list[str]
    stats: list[dict[str, Any]]
    user_notes: str | None


class VenueConfig(TypedDict, total=False):
    name: str | None
    template: str | None
    citation_style: str
    page_limit: int | None


class ApprovalRecord(TypedDict):
    action: Literal["approve", "reject"]
    at: str  # ISO timestamp
    by: str | None  # user id
    comment: str | None


class StageOutput(TypedDict):
    """A stage's latest draft. Stub agents only produce `draft`; real agents add structured fields."""

    draft: str
    agent: str
    generated_at: str


class ResearchState(TypedDict, total=False):
    # ── B12 ─────────────────────────────────────────────
    project_id: str
    current_stage: str
    idea: IdeaBlock
    problem: ProblemBlock
    objectives: list[Objective]
    research_questions: list[ResearchQuestion]
    title: str | None
    abstract_draft: str | None
    selected_paper_ids: list[str]
    comparison_matrix_id: str | None
    gap_ids: list[str]
    methodology: MethodologyBlock
    results: ResultsBlock
    section_ids: dict[str, str]
    reference_keys: list[str]
    venue: VenueConfig
    approvals: Annotated[dict[str, ApprovalRecord], merge_dicts]
    pending_feedback: str | None
    stale_stages: list[str]
    # ── orchestration ───────────────────────────────────
    requested_stage: NotRequired[str]  # the stage this run executes
    reopen: NotRequired[bool]  # "go back": route straight to review without re-running the agent
    stage_status: Annotated[dict[str, str], merge_dicts]  # stage -> StageStatus value
    stage_outputs: Annotated[dict[str, StageOutput], merge_dicts]  # drafts until sections exist (Step 2.3)


def initial_state(
    project_id: str,
    raw_idea: str | None,
    domain: str | None,
    target_venue: str | None,
    citation_style: str,
) -> ResearchState:
    return ResearchState(
        project_id=project_id,
        current_stage=StageName.IDEA.value,
        idea=IdeaBlock(raw_idea=raw_idea, refined_idea=None, domain=domain, keywords=[]),
        problem=ProblemBlock(statement=None, motivation=None, scope=None, contributions=[]),
        objectives=[],
        research_questions=[],
        title=None,
        abstract_draft=None,
        selected_paper_ids=[],
        comparison_matrix_id=None,
        gap_ids=[],
        methodology=MethodologyBlock(),
        results=ResultsBlock(),
        section_ids={},
        reference_keys=[],
        venue=VenueConfig(name=target_venue, template=target_venue, citation_style=citation_style, page_limit=None),
        approvals={},
        pending_feedback=None,
        stale_stages=[],
        stage_status={s.value: "not_started" for s in StageName},
        stage_outputs={},
    )
