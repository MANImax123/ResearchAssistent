"""Runs stages through the graph and keeps the database in step with it.

The checkpointed graph state is the source of truth for the review flow; after every run or
resume it is mirrored to `research_state` (snapshot + version) and the `stages` / `projects` rows.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.models import (
    File,
    MethodologyInput,
    MethodologyQuestion,
    Paper,
    Project,
    ResearchGap,
    ResearchStateSnapshot,
    Section,
    Stage,
)
from app.db.models.enums import FileKind, GapStatus, StageName, StageStatus, VersionSource
from app.orchestrator import workflow
from app.orchestrator.checkpoints import CheckpointerFactory, open_checkpointer, thread_config
from app.orchestrator.graph import REVIEW_NODE, StageAgent, build_graph, now_iso
from app.orchestrator.state import ResearchState, initial_state
from app.services import sections

log = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Tests point this at the test database
checkpointer_factory: CheckpointerFactory = open_checkpointer


class ReviewPendingError(Exception):
    def __init__(self, stage: str) -> None:
        super().__init__(f"Stage '{stage}' is awaiting review")
        self.stage = stage


class NoPendingReviewError(Exception):
    pass


class PrerequisitesNotMetError(Exception):
    def __init__(self, stage: str, missing: list[str]) -> None:
        super().__init__(f"Stage '{stage}' needs approved: {', '.join(missing)}")
        self.stage = stage
        self.missing = missing


class InvalidStageActionError(Exception):
    """The action doesn't apply to the stage in its current state."""


@dataclass(frozen=True)
class PendingReview:
    stage: str
    draft: str | None


@dataclass(frozen=True)
class GraphSnapshot:
    values: dict[str, Any]
    pending: PendingReview | None
    checkpoint_id: str | None
    next: tuple[str, ...]


async def _snapshot(graph: Any, project_id: uuid.UUID) -> GraphSnapshot:
    state = await graph.aget_state(thread_config(project_id))
    pending = None
    for task in state.tasks:
        for intr in task.interrupts:
            value = intr.value or {}
            pending = PendingReview(stage=str(value.get("stage", "")), draft=value.get("draft"))
    checkpoint_id = (state.config or {}).get("configurable", {}).get("checkpoint_id")
    return GraphSnapshot(values=dict(state.values or {}), pending=pending, checkpoint_id=checkpoint_id, next=state.next)


async def read_state(project_id: uuid.UUID, factory: CheckpointerFactory | None = None) -> GraphSnapshot:
    async with (factory or checkpointer_factory)() as saver:
        return await _snapshot(build_graph(saver), project_id)


async def delete_thread(project_id: uuid.UUID, factory: CheckpointerFactory | None = None) -> None:
    """Remove the project's checkpointed graph state (on project deletion)."""
    async with (factory or checkpointer_factory)() as saver:
        await saver.adelete_thread(str(project_id))


async def run_stage(
    project_id: uuid.UUID,
    stage: StageName,
    *,
    session_factory: SessionFactory,
    feedback: str | None = None,
    factory: CheckpointerFactory | None = None,
    agents: dict[StageName, StageAgent] | None = None,
) -> GraphSnapshot:
    """Run (or re-run) one stage's agent; the graph then pauses at human review.

    Prerequisites must be approved. Re-running a stage marks its started dependents stale.
    `feedback` (e.g. from Regenerate) is passed to the agent as `pending_feedback`.
    """
    async with session_factory() as session:
        project = await _load_project(session, project_id)
        fresh = _researcher_inputs(project)
        selected = await selected_paper_ids(session, project_id)
        gap_ids = await accepted_gap_ids(session, project_id)
        methodology = await methodology_state(session, project_id)

    async with (factory or checkpointer_factory)() as saver:
        graph = build_graph(saver, agents if agents is not None else _default_agents(session_factory))
        current = await _snapshot(graph, project_id)
        if current.pending is not None:
            # New input would silently discard the pending review, so refuse
            raise ReviewPendingError(current.pending.stage)
        statuses = _statuses(current.values)
        missing = workflow.blocked_by(stage, statuses)
        if missing:
            raise PrerequisitesNotMetError(stage.value, [m.value for m in missing])
        if current.values:
            idea = {**current.values.get("idea", {}), **fresh["idea"]}
            venue = {**current.values.get("venue", {}), **fresh["venue"]}
            inputs: dict[str, Any] = {"requested_stage": stage.value, "idea": idea, "venue": venue}
        else:
            inputs = {**fresh["initial"], "requested_stage": stage.value}
        inputs["selected_paper_ids"] = selected
        inputs["gap_ids"] = gap_ids
        inputs["methodology"] = methodology
        stale = workflow.stale_after_change(stage, statuses)
        if stale:
            inputs["stage_status"] = {d.value: StageStatus.STALE.value for d in stale}
        if feedback is not None:
            inputs["pending_feedback"] = feedback
        await graph.ainvoke(inputs, thread_config(project_id))
        snapshot = await _snapshot(graph, project_id)

    await sync_to_db(project_id, snapshot, session_factory, drafted=stage)
    log.info("stage_run_paused", project_id=str(project_id), stage=stage.value, stale=[d.value for d in stale])
    return snapshot


async def go_back(
    project_id: uuid.UUID,
    stage: StageName,
    *,
    session_factory: SessionFactory,
    factory: CheckpointerFactory | None = None,
) -> GraphSnapshot:
    """Reopen an earlier stage for review (its draft is kept); every started dependent becomes stale."""
    async with (factory or checkpointer_factory)() as saver:
        graph = build_graph(saver)
        current = await _snapshot(graph, project_id)
        statuses = _statuses(current.values)
        if statuses.get(stage, StageStatus.NOT_STARTED) in (StageStatus.NOT_STARTED, StageStatus.RUNNING):
            raise InvalidStageActionError(f"Stage '{stage.value}' hasn't produced a draft to go back to")
        if current.pending is not None:
            pending = StageName(current.pending.stage)
            if pending == stage:
                raise InvalidStageActionError(f"Stage '{stage.value}' is already awaiting review")
            # Going back discards the pending review; only allowed if that stage depends on this one
            if pending not in workflow.dependents(stage):
                raise ReviewPendingError(current.pending.stage)
        stale = {d.value: StageStatus.STALE.value for d in workflow.stale_after_change(stage, statuses)}
        changes = {**stale, stage.value: StageStatus.AWAITING_REVIEW.value}
        merged = {**{k.value: v.value for k, v in statuses.items()}, **changes}
        inputs = {
            "requested_stage": stage.value,
            "reopen": True,
            "current_stage": stage.value,
            "stage_status": changes,
            "stale_stages": [s.value for s in StageName if merged.get(s.value) == StageStatus.STALE.value],
        }
        await graph.ainvoke(inputs, thread_config(project_id))
        snapshot = await _snapshot(graph, project_id)

    await sync_to_db(project_id, snapshot, session_factory)
    log.info("stage_reopened", project_id=str(project_id), stage=stage.value, stale=sorted(stale))
    return snapshot


async def amend(
    project_id: uuid.UUID,
    stage: StageName,
    fields: dict[str, Any],
    *,
    draft: str | None = None,
    session_factory: SessionFactory,
    factory: CheckpointerFactory | None = None,
) -> GraphSnapshot:
    """Change an approved stage's structured fields outside a review (e.g. the researcher accepted a
    plan refinement in Step 4.10). The stage stays approved; every started dependent becomes stale,
    and a dependent's pending review is closed (its draft is kept)."""
    async with (factory or checkpointer_factory)() as saver:
        graph = build_graph(saver)
        current = await _snapshot(graph, project_id)
        statuses = _statuses(current.values)
        if statuses.get(stage) != StageStatus.APPROVED:
            raise InvalidStageActionError(f"Stage '{stage.value}' isn't approved; edit it in its review instead")
        if any(s == StageStatus.RUNNING for s in statuses.values()):
            raise InvalidStageActionError("A stage is running; wait for it to finish")
        if current.pending is not None and StageName(current.pending.stage) not in workflow.dependents(stage):
            raise ReviewPendingError(current.pending.stage)
        stale = {d.value: StageStatus.STALE.value for d in workflow.stale_after_change(stage, statuses)}
        merged = {**{k.value: v.value for k, v in statuses.items()}, **stale}
        update: dict[str, Any] = {
            **fields,
            "stage_status": stale,
            "stale_stages": [s.value for s in StageName if merged.get(s.value) == StageStatus.STALE.value],
        }
        output = current.values.get("stage_outputs", {}).get(stage.value)
        if output is not None and draft is not None:
            update["stage_outputs"] = {stage.value: {**output, "draft": draft, "generated_at": now_iso()}}
        # Applied as the review node: it has no outgoing edges, so any paused review ends here
        await graph.aupdate_state(thread_config(project_id), update, as_node=REVIEW_NODE)
        snapshot = await _snapshot(graph, project_id)

    await sync_to_db(project_id, snapshot, session_factory)
    log.info("stage_amended", project_id=str(project_id), stage=stage.value, stale=sorted(stale))
    return snapshot


async def review(
    project_id: uuid.UUID,
    decision: dict[str, Any],
    *,
    session_factory: SessionFactory,
    factory: CheckpointerFactory | None = None,
    agents: dict[StageName, StageAgent] | None = None,
) -> GraphSnapshot:
    """Resume the paused run with the researcher's decision: approve | reject | edit | regenerate.

    Regenerate re-runs the stage's agent before pausing again, so call it from a background job.
    """
    async with (factory or checkpointer_factory)() as saver:
        graph = build_graph(saver, agents if agents is not None else _default_agents(session_factory))
        current = await _snapshot(graph, project_id)
        if current.pending is None:
            raise NoPendingReviewError("No stage is awaiting review")
        await graph.ainvoke(Command(resume=decision), thread_config(project_id))
        snapshot = await _snapshot(graph, project_id)

    action = decision.get("action")
    drafted = StageName(current.pending.stage) if action in ("edit", "regenerate") else None
    by = decision.get("by")
    author = uuid.UUID(by) if action == "edit" and by else None
    await sync_to_db(project_id, snapshot, session_factory, drafted=drafted, author=author)
    log.info(
        "stage_review_resumed", project_id=str(project_id), stage=current.pending.stage, action=decision.get("action")
    )
    return snapshot


async def sync_to_db(
    project_id: uuid.UUID,
    snapshot: GraphSnapshot,
    session_factory: SessionFactory,
    *,
    drafted: StageName | None = None,
    author: uuid.UUID | None = None,
) -> None:
    """Mirror graph state into research_state / stages / sections / projects.

    `drafted`: the stage whose draft just changed; its text becomes a new version of the stage's
    section (an AI version, or the researcher's edit when `author` is set).
    """
    values = snapshot.values
    statuses: dict[str, str] = values.get("stage_status", {})
    approvals: dict[str, dict[str, Any]] = values.get("approvals", {})
    async with session_factory() as session:
        project = await _load_project(session, project_id)
        if project.research_state is None:
            project.research_state = ResearchStateSnapshot(snapshot={}, version=0)
        # The paper selection and gap decisions live in their tables (edited outside the graph); the mirror shows them
        project.research_state.snapshot = {
            **values,
            "selected_paper_ids": await selected_paper_ids(session, project_id),
            "gap_ids": await accepted_gap_ids(session, project_id),
            "methodology": await methodology_state(session, project_id),
        }
        project.research_state.version += 1
        for row in project.stages:
            status = statuses.get(row.name.value)
            if status and status != row.status.value:
                row.status = StageStatus(status)
            if row.status == StageStatus.APPROVED and (record := approvals.get(row.name.value)):
                row.approved_at = datetime.fromisoformat(record["at"])
                row.approved_by = uuid.UUID(record["by"]) if record.get("by") else None
            elif row.status != StageStatus.APPROVED:
                row.approved_at = row.approved_by = None
        if values.get("current_stage"):
            project.current_stage = StageName(values["current_stage"])
        if drafted is not None:
            await _record_draft(session, project_id, drafted, values, author)
        await _sync_section_statuses(session, project_id, project.stages)
        await session.commit()


async def _record_draft(
    session: AsyncSession, project_id: uuid.UUID, stage: StageName, values: dict[str, Any], author: uuid.UUID | None
) -> None:
    section_type = sections.STAGE_SECTIONS.get(stage)
    output = values.get("stage_outputs", {}).get(stage.value)
    if section_type is None or not output:
        return
    text = output["section_draft"] if "section_draft" in output else output["draft"]
    if text is None:
        return  # this stage's output isn't section text (e.g. the research plan)
    section = await sections.get_or_create_section(session, project_id, section_type)
    current = await sections.current_version(session, section)
    if current is not None and current.content == text:
        return
    if author is not None:
        await sections.add_version(
            session,
            section,
            text,
            sections.edited_source(current),
            created_by=author,
            change_note="Edited in review",
        )
    else:
        await sections.add_version(
            session, section, text, VersionSource.AI, change_note=f"Drafted by {output['agent']} agent"
        )


async def _sync_section_statuses(session: AsyncSession, project_id: uuid.UUID, stages: list[Stage]) -> None:
    by_stage = {row.name: row.status for row in stages}
    rows = await session.scalars(select(Section).where(Section.project_id == project_id))
    for section in rows:
        stage = sections.SECTION_STAGE.get(section.type)
        if stage is None or section.current_version_id is None:
            continue
        section.status = sections.SECTION_STATUS_FOR_STAGE[by_stage[stage]]


async def set_stage_status(
    project_id: uuid.UUID, stage: StageName, status: StageStatus, session_factory: SessionFactory
) -> None:
    async with session_factory() as session:
        row = await session.scalar(select(Stage).where(Stage.project_id == project_id, Stage.name == stage))
        if row is not None:
            row.status = status
            await session.commit()


def _default_agents(session_factory: SessionFactory) -> dict[StageName, StageAgent]:
    from app.agents import registry  # imported lazily: agents import orchestrator modules

    return registry.agent_factory(session_factory)


def _statuses(values: dict[str, Any]) -> dict[StageName, StageStatus]:
    raw: dict[str, str] = values.get("stage_status", {})
    return {StageName(k): StageStatus(v) for k, v in raw.items()}


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.stages), selectinload(Project.research_state))
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise LookupError(f"Project {project_id} not found")
    return project


async def selected_paper_ids(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """The researcher's paper selection (B12 `selected_paper_ids`): must-include first, then by relevance."""
    rows = await session.scalars(
        select(Paper.id)
        .where(Paper.project_id == project_id, Paper.selected.is_(True))
        .order_by(
            Paper.must_include.desc(),
            Paper.relevance_score.desc().nulls_last(),
            Paper.similarity.desc().nulls_last(),
            Paper.created_at,
        )
    )
    return [str(pid) for pid in rows]


async def accepted_gap_ids(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """The research state's `gap_ids` (B12): the gaps the researcher accepted, oldest first."""
    rows = await session.scalars(
        select(ResearchGap.id)
        .where(ResearchGap.project_id == project_id, ResearchGap.status == GapStatus.ACCEPTED)
        .order_by(ResearchGap.created_at, ResearchGap.id)
    )
    return [str(i) for i in rows]


async def methodology_state(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    """The research state's `methodology` block (B12): the researcher's input (Step 5.1), the follow-up
    questions they answered, and the uploaded diagrams."""
    row = await session.get(MethodologyInput, project_id)
    answered = await session.scalars(
        select(MethodologyQuestion)
        .where(MethodologyQuestion.project_id == project_id, MethodologyQuestion.status == "answered")
        .order_by(MethodologyQuestion.created_at)
    )
    diagrams = await session.scalars(
        select(File.id).where(File.project_id == project_id, File.kind == FileKind.DIAGRAM).order_by(File.created_at)
    )
    return {
        **(row.data if row is not None else {}),
        "clarifications": [{"field": q.field, "question": q.question, "answer": q.answer} for q in answered],
        "diagrams": [str(i) for i in diagrams],
    }


def _researcher_inputs(project: Project) -> dict[str, Any]:
    """Fields the researcher edits outside the graph; refreshed into the state on every run."""
    snapshot = project.research_state.snapshot if project.research_state else {}
    raw_idea = (snapshot.get("idea") or {}).get("raw_idea")
    initial: ResearchState = initial_state(
        str(project.id), raw_idea, project.domain, project.target_venue, project.citation_style
    )
    return {
        "initial": initial,
        # An empty project domain must not wipe the domain the Idea agent wrote into the plan
        "idea": {"raw_idea": raw_idea, **({"domain": project.domain} if project.domain else {})},
        "venue": {
            "name": project.target_venue,
            "template": project.target_venue,
            "citation_style": project.citation_style,
        },
    }
