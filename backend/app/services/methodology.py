"""Methodology input workspace (Step 5.1): the researcher's details, uploads and follow-up questions.

The input lives in its own table (edited outside the graph) and is mirrored into the research state's
`methodology` block, like the paper selection, so the Methodology agent (5.3) reads it from the state.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.idea.agent import plan_from_state
from app.agents.methodology import follow_up
from app.agents.methodology.schemas import MethodologyInputData, missing
from app.core.logging import get_logger
from app.db.models import File, MethodologyInput, MethodologyQuestion, Project, ResearchStateSnapshot
from app.db.models.enums import FileKind, StageName, StageStatus
from app.orchestrator.service import methodology_state
from app.services.context import format_context, load_context
from app.services.llm import LLMService, SessionFactory
from app.services.storage import Storage

log = get_logger(__name__)


class VersionConflictError(Exception):
    def __init__(self, current: int) -> None:
        super().__init__(f"The methodology was changed elsewhere (now version {current}); reload and try again")
        self.current = current


def blocker(project: Project) -> str | None:
    """Why the methodology input can't be changed now, or None."""
    statuses = {row.name: row.status for row in project.stages}
    if statuses.get(StageName.IDEA) != StageStatus.APPROVED:
        return "Approve the research plan (Idea stage) first"
    stage = statuses.get(StageName.METHODOLOGY)
    if stage == StageStatus.RUNNING:
        return "The Methodology stage is running"
    if stage == StageStatus.APPROVED:
        return "Go back to the Methodology stage to change its inputs"
    return None


async def load(session: AsyncSession, project_id: uuid.UUID) -> tuple[MethodologyInputData, int, datetime | None]:
    row = await session.get(MethodologyInput, project_id)
    if row is None:
        return MethodologyInputData(), 0, None
    return MethodologyInputData.model_validate(row.data or {}), row.version, row.updated_at


async def file_counts(session: AsyncSession, project_id: uuid.UUID) -> dict[FileKind, int]:
    rows = await session.execute(
        select(File.kind, func.count()).where(File.project_id == project_id).group_by(File.kind)
    )
    return {kind: n for kind, n in rows.all()}


async def checklist(session: AsyncSession, project_id: uuid.UUID, data: MethodologyInputData) -> list[Any]:
    counts = await file_counts(session, project_id)
    return missing(data, diagrams=counts.get(FileKind.DIAGRAM, 0), code_files=counts.get(FileKind.CODE, 0))


async def sync_state(session: AsyncSession, project: Project) -> None:
    """Mirror the input (+ answered questions, diagrams) into the research state now."""
    await session.refresh(project, ["research_state"])
    block = await methodology_state(session, project.id)
    if project.research_state is None:
        project.research_state = ResearchStateSnapshot(snapshot={"project_id": str(project.id), "methodology": block})
        return
    if project.research_state.snapshot.get("methodology") != block:
        project.research_state.snapshot = {**project.research_state.snapshot, "methodology": block}
        project.research_state.version += 1


async def save(
    session: AsyncSession, project: Project, data: MethodologyInputData, by: uuid.UUID, base_version: int | None
) -> MethodologyInput:
    row = await session.get(MethodologyInput, project.id, with_for_update=True)
    current = row.version if row is not None else 0
    if base_version is not None and base_version != current:
        raise VersionConflictError(current)
    payload = data.model_dump(mode="json")
    if row is None:
        row = MethodologyInput(project_id=project.id, data=payload, version=1, updated_by=by)
        session.add(row)
    elif row.data != payload:
        row.data = payload
        row.version += 1
        row.updated_by = by
    await session.flush()
    await sync_state(session, project)
    return row


async def ask_follow_ups(
    project_id: uuid.UUID, session_factory: SessionFactory, llm: LLMService, storage: Storage
) -> dict[str, Any]:
    """Replace the open questions with new ones for what is missing now."""
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise LookupError("Project not found")
        await session.refresh(project, ["research_state"])
        plan = plan_from_state(project.research_state.snapshot if project.research_state else {})
        data, _, _ = await load(session, project_id)
        found = await checklist(session, project_id, data)
        documents = await load_context(session, storage, project_id, kinds=(FileKind.NOTES, FileKind.CODE))
        diagrams = [
            f.filename
            for f in await session.scalars(
                select(File).where(File.project_id == project_id, File.kind == FileKind.DIAGRAM)
            )
        ]
        answered = [
            (q.question, q.answer or "")
            for q in await session.scalars(
                select(MethodologyQuestion)
                .where(MethodologyQuestion.project_id == project_id, MethodologyQuestion.status == "answered")
                .order_by(MethodologyQuestion.created_at)
            )
        ]
    files = format_context(documents)
    if diagrams:
        files += f"\n\nDiagrams uploaded (not read yet): {', '.join(diagrams)}"
    questions = await follow_up.ask(data, found, plan, files.strip(), answered, llm, project_id)
    async with session_factory() as session:
        await session.execute(
            delete(MethodologyQuestion).where(
                MethodologyQuestion.project_id == project_id, MethodologyQuestion.status == "open"
            )
        )
        for q in questions:
            session.add(MethodologyQuestion(project_id=project_id, field=q.field, question=q.question,
                                            reason=q.reason or None, kind=q.kind, status="open"))  # fmt: skip
        await session.commit()
    log.info("methodology_follow_ups", project_id=str(project_id), missing=len(found), asked=len(questions))
    return {"missing": len(found), "asked": len(questions)}


async def update_question(
    session: AsyncSession, project: Project, question: MethodologyQuestion, *, answer: str | None, status: str
) -> MethodologyQuestion:
    question.status = status
    question.answer = answer if status == "answered" else question.answer
    question.answered_at = datetime.now(UTC) if status == "answered" else None
    await session.flush()
    await sync_state(session, project)
    return question
