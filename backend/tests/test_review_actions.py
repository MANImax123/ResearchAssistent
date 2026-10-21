"""Step 2.2: prerequisite gating, review actions (approve/reject/edit/regenerate/go back), staleness, feedback rows."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Feedback, ProjectMember, User
from app.db.models.enums import FeedbackAction, MemberRole, StageName
from app.db.models.enums import StageName as S
from app.db.models.enums import StageStatus as St
from app.orchestrator import service as orchestrator
from app.orchestrator import workflow
from tests.helpers import IDEA, Flow, auth_headers, factory_for

pytestmark = pytest.mark.usefixtures("checkpointer")


# ─── Workflow rules (pure) ───────────────────────────────────────────


def test_prerequisites_and_dependents() -> None:
    assert workflow.PREREQUISITES[S.IDEA] == ()
    assert workflow.dependents(S.RESULTS) == [S.INTRODUCTION, S.CONCLUSION, S.REFERENCES, S.REVIEW, S.FORMATTING]
    assert workflow.dependents(S.IDEA) == list(S)[1:]
    assert workflow.dependents(S.FORMATTING) == []
    assert workflow.blocked_by(S.REFERENCES, {S.INTRODUCTION: St.APPROVED, S.CONCLUSION: St.STALE}) == [S.CONCLUSION]


def test_only_started_dependents_go_stale() -> None:
    statuses = {S.IDEA: St.APPROVED, S.LITERATURE: St.APPROVED, S.METHODOLOGY: St.AWAITING_REVIEW}
    assert workflow.stale_after_change(S.IDEA, statuses) == [S.LITERATURE, S.METHODOLOGY]
    assert workflow.stale_after_change(S.METHODOLOGY, statuses) == []


# ─── Spec tests ─────────────────────────────────────────────────────


async def test_prerequisite_gating(flow: Flow) -> None:
    blocked = await flow.post("/stages/literature/run")
    assert blocked.status_code == 409 and blocked.json()["detail"] == "Approve Idea & Problem first"

    await flow.run("idea")
    still_blocked = await flow.post("/stages/literature/run")  # idea awaits review, not approved
    assert still_blocked.status_code == 409

    await flow.act("idea", "approve")
    await flow.run("literature")
    assert (await flow.statuses())["literature"] == "awaiting_review"

    # The service enforces it too (backstop for concurrent requests)
    await flow.act("literature", "approve")
    with pytest.raises(orchestrator.PrerequisitesNotMetError) as exc:
        await orchestrator.run_stage(flow.pid, S.RESULTS, session_factory=factory_for(flow.db))
    assert exc.value.missing == ["methodology"]


async def test_regenerate_passes_feedback_to_the_agent(flow: Flow) -> None:
    await flow.run("idea")

    started = await flow.act("idea", "regenerate", {"feedback": "Focus on kinase inhibitors"}, expect=202)

    assert started["status"] == "running" and started["job"]["type"] == "stage_run"
    state = await flow.state()
    assert state["pending_review"]["stage"] == "idea"
    assert "Feedback to address: Focus on kinase inhibitors" in state["pending_review"]["draft"]
    assert state["state"]["pending_feedback"] == "Focus on kinase inhibitors"
    assert ("idea", "regenerate", "Focus on kinase inhibitors") in await flow.feedback()


async def test_go_back_marks_downstream_stale(flow: Flow) -> None:
    """Acceptance: approve stage 1, run 2, go back to 1, edit, see stage 2 marked stale."""
    await flow.run("idea")
    await flow.act("idea", "approve", {"comment": "Good start"})
    await flow.run("literature")
    assert (await flow.statuses())["literature"] == "awaiting_review"

    back = await flow.act("idea", "go-back")
    statuses = {s["name"]: s["status"] for s in back["stages"]}
    assert statuses["idea"] == "awaiting_review" and statuses["literature"] == "stale"
    assert back["pending_review"]["stage"] == "idea"
    assert back["state"]["stale_stages"] == ["literature"]
    assert statuses["methodology"] == "not_started"  # never started: nothing to invalidate

    edited = await flow.act("idea", "edit", {"content": "My own problem statement.", "change_note": "rewrote"})
    assert edited["pending_review"]["draft"] == "My own problem statement."
    assert edited["state"]["stage_outputs"]["idea"]["agent"] == "user"

    approved = await flow.act("idea", "approve")
    final = {s["name"]: s for s in approved["stages"]}
    assert final["idea"]["status"] == "approved" and final["literature"]["status"] == "stale"
    assert final["literature"]["can_run"] is True  # re-run it to bring it up to date

    assert [(stage, action) for stage, action, _ in await flow.feedback()] == [
        ("idea", "approve"),
        ("idea", "back"),
        ("idea", "edit"),
        ("idea", "approve"),
    ]


# ─── Other actions and rules ─────────────────────────────────────────


async def test_reject_needs_a_comment_which_reaches_the_next_run(flow: Flow) -> None:
    await flow.run("idea")
    assert (await flow.post("/stages/idea/reject", {})).status_code == 422

    rejected = await flow.act("idea", "reject", {"comment": "Too broad"})
    assert {s["name"]: s["status"] for s in rejected["stages"]}["idea"] == "rejected"
    assert rejected["pending_review"] is None

    await flow.run("idea")
    assert "Too broad" in (await flow.state())["pending_review"]["draft"]


async def test_regenerating_an_approved_stage_makes_dependents_stale(flow: Flow) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    await flow.run("literature")
    await flow.act("literature", "approve")

    await flow.act("idea", "regenerate", {"feedback": "Add a second objective"}, expect=202)

    statuses = await flow.statuses()
    assert statuses["idea"] == "awaiting_review" and statuses["literature"] == "stale"


async def test_actions_refused_in_the_wrong_state(flow: Flow) -> None:
    assert (await flow.post("/stages/idea/approve")).status_code == 409  # nothing to review
    assert (await flow.post("/stages/idea/regenerate")).status_code == 409  # never run
    assert (await flow.post("/stages/idea/go-back")).status_code == 409  # no draft yet

    await flow.run("idea")
    assert (await flow.post("/stages/idea/run")).status_code == 409  # awaiting review: use regenerate
    assert (await flow.post("/stages/idea/go-back")).status_code == 409  # already in review
    await flow.act("idea", "approve")

    edit = await flow.post("/stages/idea/edit", {"content": "x"})
    assert edit.status_code == 409  # approved: go back first
    rerun = await flow.post("/stages/idea/run")
    assert rerun.status_code == 409 and "Regenerate or Go back" in rerun.json()["detail"]
    assert (await flow.post("/stages/idea/edit", {"content": "   "})).status_code == 422


async def test_cannot_go_back_past_an_unrelated_pending_review(flow: Flow) -> None:
    # Approve the chain up to results, then put introduction and conclusion through review
    for stage in ("idea", "literature", "methodology", "results", "introduction"):
        await flow.run(stage)
        await flow.act(stage, "approve")
    await flow.run("conclusion")  # awaiting review; it doesn't depend on introduction

    resp = await flow.post("/stages/introduction/go-back")
    assert resp.status_code == 409 and "Conclusion" in resp.json()["detail"]
    intro = next(s for s in (await flow.state())["stages"] if s["name"] == "introduction")
    assert intro["can_go_back"] is False


async def test_stage_flags_guide_the_ui(flow: Flow) -> None:
    fresh = {s["name"]: s for s in (await flow.state())["stages"]}
    assert fresh["idea"]["can_run"] is True
    assert fresh["literature"]["can_run"] is False and fresh["literature"]["blocked_by"] == ["idea"]
    assert fresh["references"]["prerequisites"] == ["introduction", "conclusion"]

    await flow.run("idea")
    reviewing = {s["name"]: s for s in (await flow.state())["stages"]}
    assert reviewing["idea"]["can_regenerate"] is True and reviewing["idea"]["can_run"] is False
    assert not any(s["can_run"] for s in reviewing.values())  # one review at a time


async def test_review_actions_need_editor_role(flow: Flow, api_client: AsyncClient, db_session: AsyncSession) -> None:
    await flow.run("idea")
    bob = await auth_headers(api_client, "bob@example.com")
    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    db_session.add(ProjectMember(project_id=flow.pid, user_id=bob_id, role=MemberRole.VIEWER))
    await db_session.flush()

    for action, body in (
        ("approve", {}),
        ("reject", {"comment": "no"}),
        ("edit", {"content": "x"}),
        ("regenerate", {}),
    ):
        resp = await api_client.post(flow.url(f"/stages/idea/{action}"), json=body, headers=bob)
        assert resp.status_code == 403, action
    assert (await api_client.get(flow.url("/state"), headers=bob)).status_code == 200


async def test_approval_records_who_and_when(flow: Flow, db_session: AsyncSession) -> None:
    await flow.run("idea")
    state = await flow.act("idea", "approve", {"comment": "ok"})

    alice_id = await db_session.scalar(select(User.id).where(User.email == "alice@example.com"))
    approval = state["state"]["approvals"]["idea"]
    assert approval["by"] == str(alice_id) and approval["comment"] == "ok"
    assert next(s for s in state["stages"] if s["name"] == "idea")["approved_at"] is not None
    assert IDEA in state["state"]["stage_outputs"]["idea"]["draft"]


async def test_feedback_rows_have_user_and_action(flow: Flow, db_session: AsyncSession) -> None:
    await flow.run("idea")
    await flow.act("idea", "reject", {"comment": "Narrower please"})

    row = await db_session.scalar(select(Feedback).where(Feedback.project_id == flow.pid))
    assert row is not None and row.action == FeedbackAction.REJECT and row.stage == StageName.IDEA
    alice_id = await db_session.scalar(select(User.id).where(User.email == "alice@example.com"))
    assert row.user_id == alice_id and row.comment == "Narrower please"
