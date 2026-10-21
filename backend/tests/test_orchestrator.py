"""Orchestrator: stage run → pause at human review → state persisted (survives a new connection) → resume."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, ResearchStateSnapshot, Stage, User
from app.db.models.enums import JobStatus, MemberRole, StageName, StageStatus
from app.main import app
from app.orchestrator import service as orchestrator
from app.orchestrator.checkpoints import thread_config
from app.orchestrator.graph import REVIEW_NODE, build_graph
from app.services.jobs import execute_job
from app.workers.dispatch import get_dispatcher
from app.workers.handlers import HANDLERS
from tests.helpers import IDEA, RecordingDispatcher, auth_headers, factory_for

pytestmark = pytest.mark.usefixtures("checkpointer")


async def stage_row(db: AsyncSession, pid: uuid.UUID, stage: StageName) -> Stage:
    row = await db.scalar(
        select(Stage).where(Stage.project_id == pid, Stage.name == stage).execution_options(populate_existing=True)
    )
    assert row is not None
    return row


# ─── The core lifecycle (spec test for Step 2.1) ─────────────────────────


async def test_run_pauses_for_review_persists_and_resumes(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    sessions = factory_for(db_session)

    # 1. Run stage 1: the stub drafts, then the graph pauses at human review
    snap = await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)
    assert snap.next == (REVIEW_NODE,)
    assert snap.pending is not None and snap.pending.stage == "idea"
    assert snap.pending.draft is not None and "Placeholder draft — Idea & Problem" in snap.pending.draft
    assert IDEA in snap.pending.draft  # the agent read the shared state
    assert snap.values["stage_status"]["idea"] == "awaiting_review"

    # 2. Mirrored to the database
    assert (await stage_row(db_session, project_id, StageName.IDEA)).status == StageStatus.AWAITING_REVIEW
    rs = await db_session.get(ResearchStateSnapshot, project_id, populate_existing=True)
    assert rs is not None and rs.version == 2  # 1 at creation, +1 per sync
    assert rs.snapshot["stage_outputs"]["idea"]["agent"] == "stub"
    assert rs.snapshot["project_id"] == str(project_id)

    # 3. A brand-new checkpointer connection (as after a restart) sees the same paused run
    restored = await orchestrator.read_state(project_id)
    assert restored.pending == snap.pending
    assert restored.checkpoint_id == snap.checkpoint_id

    # 4. Resume with approval: the run continues to the end
    done = await orchestrator.review(
        project_id, {"action": "approve", "by": None, "comment": "looks good"}, session_factory=sessions
    )
    assert done.pending is None and done.next == ()
    assert done.values["stage_status"]["idea"] == "approved"
    assert done.values["approvals"]["idea"]["action"] == "approve"
    row = await stage_row(db_session, project_id, StageName.IDEA)
    assert row.status == StageStatus.APPROVED and row.approved_at is not None

    # 5. The next stage runs on the same thread, keeping earlier state
    nxt = await orchestrator.run_stage(project_id, StageName.LITERATURE, session_factory=sessions)
    assert nxt.pending is not None and nxt.pending.stage == "literature"
    assert nxt.values["stage_status"]["idea"] == "approved"
    assert set(nxt.values["stage_outputs"]) == {"idea", "literature"}
    project = await db_session.get(Project, project_id, populate_existing=True)
    assert project is not None and project.current_stage == StageName.LITERATURE


async def test_deleting_project_deletes_its_graph_thread(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession, project_id: uuid.UUID
) -> None:
    await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=factory_for(db_session))
    assert (await orchestrator.read_state(project_id)).checkpoint_id is not None

    assert (await api_client.delete(f"/api/v1/projects/{project_id}", headers=alice)).status_code == 204

    gone = await orchestrator.read_state(project_id)
    assert gone.checkpoint_id is None and gone.values == {}


async def test_cannot_start_another_stage_while_review_pending(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    sessions = factory_for(db_session)
    await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)

    with pytest.raises(orchestrator.ReviewPendingError) as exc:
        await orchestrator.run_stage(project_id, StageName.LITERATURE, session_factory=sessions)
    assert exc.value.stage == "idea"
    # the pending review is untouched
    assert (await orchestrator.read_state(project_id)).pending is not None


async def test_resume_without_pending_review_fails(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    with pytest.raises(orchestrator.NoPendingReviewError):
        await orchestrator.review(project_id, {"action": "approve"}, session_factory=factory_for(db_session))


async def test_reject_feeds_comment_into_the_next_run(db_session: AsyncSession, project_id: uuid.UUID) -> None:
    sessions = factory_for(db_session)
    await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)

    rejected = await orchestrator.review(
        project_id, {"action": "reject", "comment": "Narrow it to kinase inhibitors"}, session_factory=sessions
    )
    assert rejected.values["stage_status"]["idea"] == "rejected"
    assert rejected.values["pending_feedback"] == "Narrow it to kinase inhibitors"
    assert (await stage_row(db_session, project_id, StageName.IDEA)).status == StageStatus.REJECTED

    again = await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)
    assert again.pending is not None and "Narrow it to kinase inhibitors" in (again.pending.draft or "")


async def test_edited_idea_reaches_the_next_run(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession, project_id: uuid.UUID
) -> None:
    sessions = factory_for(db_session)
    await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)
    await orchestrator.review(project_id, {"action": "approve"}, session_factory=sessions)

    await api_client.patch(
        f"/api/v1/projects/{project_id}", json={"raw_idea": "Protein folding with diffusion"}, headers=alice
    )
    snap = await orchestrator.run_stage(project_id, StageName.IDEA, session_factory=sessions)

    assert "Protein folding with diffusion" in (snap.pending.draft if snap.pending else "")
    assert snap.values["idea"]["raw_idea"] == "Protein folding with diffusion"


# ─── API ──────────────────────────────────────────────────────────────


async def test_run_endpoint_queues_job_then_state_shows_review(
    api_client: AsyncClient,
    alice: dict[str, str],
    db_session: AsyncSession,
    project_id: uuid.UUID,
    dispatcher: RecordingDispatcher,
) -> None:
    resp = await api_client.post(f"/api/v1/projects/{project_id}/stages/idea/run", headers=alice)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert (body["stage"], body["status"], body["job"]["type"], body["job"]["status"]) == (
        "idea",
        "running",
        "stage_run",
        "pending",
    )
    assert (await stage_row(db_session, project_id, StageName.IDEA)).status == StageStatus.RUNNING
    job_id, job_type, params = dispatcher.queued[0]
    assert params == {"project_id": str(project_id), "stage": "idea", "previous_status": "not_started", "mode": "run"}

    # The worker's part, in-process
    assert await execute_job(job_id, HANDLERS[job_type], params, factory_for(db_session)) == JobStatus.SUCCESS
    job = (await api_client.get(f"/api/v1/jobs/{job_id}", headers=alice)).json()
    assert job["status"] == "success" and job["result"]["status"] == "awaiting_review"

    state = (await api_client.get(f"/api/v1/projects/{project_id}/state", headers=alice)).json()
    assert state["pending_review"]["stage"] == "idea"
    assert "Placeholder draft" in state["pending_review"]["draft"]
    assert state["checkpoint"]["thread_id"] == str(project_id) and state["checkpoint"]["next"] == [REVIEW_NODE]
    assert state["version"] == 2
    assert state["state"]["stage_status"]["idea"] == "awaiting_review"
    assert [s["name"] for s in state["stages"]][:2] == ["idea", "literature"]
    assert state["stages"][0]["status"] == "awaiting_review"

    # Another run is refused while the review is pending
    again = await api_client.post(f"/api/v1/projects/{project_id}/stages/literature/run", headers=alice)
    assert again.status_code == 409 and "awaiting your review" in again.json()["detail"]


async def test_state_before_any_run(api_client: AsyncClient, alice: dict[str, str], project_id: uuid.UUID) -> None:
    state = (await api_client.get(f"/api/v1/projects/{project_id}/state", headers=alice)).json()

    assert state["checkpoint"] is None and state["pending_review"] is None
    assert state["state"]["idea"]["raw_idea"] == IDEA
    assert {s["status"] for s in state["stages"]} == {"not_started"}


async def test_refuses_while_a_stage_is_running(
    api_client: AsyncClient, alice: dict[str, str], project_id: uuid.UUID, dispatcher: RecordingDispatcher
) -> None:
    assert (await api_client.post(f"/api/v1/projects/{project_id}/stages/idea/run", headers=alice)).status_code == 202
    resp = await api_client.post(f"/api/v1/projects/{project_id}/stages/idea/run", headers=alice)
    assert resp.status_code == 409 and "is running" in resp.json()["detail"]


async def test_failed_run_restores_previous_status(
    api_client: AsyncClient,
    alice: dict[str, str],
    db_session: AsyncSession,
    project_id: uuid.UUID,
    dispatcher: RecordingDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await api_client.post(f"/api/v1/projects/{project_id}/stages/idea/run", headers=alice)
    job_id, job_type, params = dispatcher.queued[0]

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agent crashed")

    monkeypatch.setattr(orchestrator, "run_stage", boom)
    assert await execute_job(job_id, HANDLERS[job_type], params, factory_for(db_session)) == JobStatus.FAILED
    assert (await stage_row(db_session, project_id, StageName.IDEA)).status == StageStatus.NOT_STARTED


async def test_enqueue_failure_does_not_leave_stage_running(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession, project_id: uuid.UUID
) -> None:
    app.dependency_overrides[get_dispatcher] = lambda: RecordingDispatcher(fail=True)
    try:
        resp = await api_client.post(f"/api/v1/projects/{project_id}/stages/idea/run", headers=alice)
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)

    assert resp.status_code == 503
    assert (await stage_row(db_session, project_id, StageName.IDEA)).status == StageStatus.NOT_STARTED


async def test_stage_permissions_and_validation(
    api_client: AsyncClient,
    alice: dict[str, str],
    db_session: AsyncSession,
    project_id: uuid.UUID,
    dispatcher: RecordingDispatcher,
) -> None:
    bob = await auth_headers(api_client, "bob@example.com")
    run_url = f"/api/v1/projects/{project_id}/stages/idea/run"
    state_url = f"/api/v1/projects/{project_id}/state"

    assert (await api_client.post(run_url, headers=bob)).status_code == 404
    assert (await api_client.get(state_url, headers=bob)).status_code == 404

    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    db_session.add(ProjectMember(project_id=project_id, user_id=bob_id, role=MemberRole.VIEWER))
    await db_session.flush()
    assert (await api_client.get(state_url, headers=bob)).status_code == 200
    assert (await api_client.post(run_url, headers=bob)).status_code == 403

    bad = await api_client.post(f"/api/v1/projects/{project_id}/stages/not-a-stage/run", headers=alice)
    assert bad.status_code == 422
    assert (await api_client.post(run_url)).status_code == 401
    assert dispatcher.queued == []


# ─── Graph wiring (in-memory checkpointer, no database) ─────────────────


@pytest.mark.parametrize("stage", list(StageName))
async def test_every_stage_routes_to_its_agent_then_review(stage: StageName) -> None:
    graph = build_graph(InMemorySaver())
    cfg = thread_config(f"t-{stage.value}")

    result = await graph.ainvoke({"requested_stage": stage.value, "stage_status": {}, "stage_outputs": {}}, cfg)

    assert result["__interrupt__"][0].value["stage"] == stage.value
    assert set(result["stage_outputs"]) == {stage.value}
    assert result["current_stage"] == stage.value


async def test_unknown_review_action_is_rejected() -> None:
    from langgraph.types import Command

    graph = build_graph(InMemorySaver())
    cfg = thread_config("t-bad")
    await graph.ainvoke({"requested_stage": "idea", "stage_status": {}, "stage_outputs": {}}, cfg)

    with pytest.raises(ValueError, match="Unsupported review action"):
        await graph.ainvoke(Command(resume={"action": "delete everything"}), cfg)
