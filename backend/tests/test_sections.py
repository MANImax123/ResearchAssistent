"""Step 2.3: section versions — create, autosave, restore, diff, edit rules, stage integration."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProjectMember, SectionVersion, User
from app.db.models.enums import MemberRole, SectionType
from tests.helpers import Flow, auth_headers

pytestmark = pytest.mark.usefixtures("checkpointer")


async def section(f: Flow, kind: str = "abstract") -> dict:  # type: ignore[type-arg]
    return (await f.client.get(f.url(f"/sections/{kind}"), headers=f.headers)).json()  # type: ignore[no-any-return]


async def versions(f: Flow, kind: str = "abstract") -> list[dict]:  # type: ignore[type-arg]
    return (await f.client.get(f.url(f"/sections/{kind}/versions"), headers=f.headers)).json()  # type: ignore[no-any-return]


async def save(
    f: Flow,
    content: str,
    *,
    autosave: bool = False,
    base: str | None = None,
    kind: str = "abstract",
    note: str | None = None,
):  # type: ignore[no-untyped-def]
    body = {"content": content, "autosave": autosave, "base_version_id": base, "change_note": note}
    return await f.client.post(f.url(f"/sections/{kind}/versions"), json=body, headers=f.headers)


# ─── Stage drafts become AI versions ───────────────────────────────────


async def test_stage_run_creates_ai_version_in_its_section(flow: Flow) -> None:
    before = {s["type"]: s for s in (await flow.client.get(flow.url("/sections"), headers=flow.headers)).json()}
    assert list(before) == [s.value for s in SectionType]
    assert before["abstract"]["exists"] is False and before["abstract"]["edit_blocked_reason"] == "Run this stage first"

    await flow.run("idea")

    abstract = await section(flow)
    assert abstract["exists"] and abstract["status"] == "draft" and abstract["stage"] == "idea"
    assert abstract["current_version"]["number"] == 1 and abstract["current_version"]["source"] == "ai"
    assert abstract["current_version"]["change_note"] == "Drafted by stub agent"
    assert "Placeholder draft — Idea & Problem" in abstract["content"]
    assert abstract["editable"] is True and abstract["word_count"] > 5


# ─── Spec: version create / restore ────────────────────────────────────


async def test_create_and_restore_versions(flow: Flow) -> None:
    await flow.run("idea")
    v1 = (await section(flow))["current_version"]

    created = await save(flow, "First rewrite of the abstract.", base=v1["id"], note="tightened")
    assert created.status_code == 201, created.text
    v2 = created.json()["version"]
    assert (v2["number"], v2["source"], v2["change_note"], created.json()["coalesced"]) == (
        2,
        "ai_edited",
        "tightened",
        False,
    )

    v3 = (await save(flow, "Second rewrite.", base=v2["id"])).json()["version"]
    assert v3["number"] == 3

    restored = await flow.client.post(flow.url(f"/sections/abstract/versions/{v1['id']}/restore"), headers=flow.headers)
    assert restored.status_code == 201
    v4 = restored.json()
    assert (v4["number"], v4["source"], v4["change_note"]) == (4, "ai", "Restored from v1")

    current = await section(flow)
    assert current["current_version"]["id"] == v4["id"]
    assert "Placeholder draft — Idea & Problem" in current["content"]
    assert [v["number"] for v in await versions(flow)] == [4, 3, 2, 1]  # nothing was lost
    old = await flow.client.get(flow.url(f"/sections/abstract/versions/{v2['id']}"), headers=flow.headers)
    assert old.json()["content"] == "First rewrite of the abstract."


# ─── Spec: diff ────────────────────────────────────────────────────────


async def test_diff_endpoint(flow: Flow) -> None:
    await flow.run("idea")
    v1 = (await section(flow))["current_version"]
    v2 = (await save(flow, "Line one\nLine two changed\nLine three", base=v1["id"])).json()["version"]
    v3 = (await save(flow, "Line one\nLine two\nLine three\nLine four", base=v2["id"])).json()["version"]

    resp = await flow.client.get(
        flow.url(f"/sections/abstract/diff?from_version={v2['id']}&to_version={v3['id']}"), headers=flow.headers
    )
    diff = resp.json()
    assert (diff["from_version"], diff["to_version"]) == (2, 3)
    assert (diff["added"], diff["removed"]) == (2, 1)
    assert "-Line two changed" in diff["unified"] and "+Line two" in diff["unified"] and "+Line four" in diff["unified"]
    assert diff["unified"].startswith("--- v2\n+++ v3")

    to_current = await flow.client.get(
        flow.url(f"/sections/abstract/diff?from_version={v1['id']}"), headers=flow.headers
    )
    assert to_current.json()["to_version"] == 3


# ─── Autosave and concurrency ──────────────────────────────────────────


async def test_autosave_updates_own_recent_autosave(flow: Flow, db_session: AsyncSession) -> None:
    await flow.run("idea")
    base = (await section(flow))["current_version"]["id"]

    first = await save(flow, "Typing…", autosave=True, base=base)
    assert first.status_code == 201 and first.json()["version"]["change_note"] == "Autosave"
    auto = first.json()["version"]

    again = await save(flow, "Typing… more words", autosave=True, base=auto["id"])
    assert again.status_code == 200 and again.json()["coalesced"] is True
    assert again.json()["version"]["id"] == auto["id"] and again.json()["version"]["content"] == "Typing… more words"

    explicit = await save(flow, "Typing… more words. Done.", base=auto["id"], note="checkpoint")
    assert explicit.status_code == 201 and explicit.json()["version"]["number"] == 3

    # An old autosave isn't reused
    stale_auto = (await save(flow, "after a break", autosave=True, base=explicit.json()["version"]["id"])).json()[
        "version"
    ]
    await db_session.execute(
        update(SectionVersion)
        .where(SectionVersion.id == uuid.UUID(stale_auto["id"]))
        .values(created_at=datetime.now(UTC) - timedelta(minutes=30))
    )
    later = await save(flow, "after a long break", autosave=True, base=stale_auto["id"])
    assert later.status_code == 201 and later.json()["version"]["number"] == 5


async def test_save_version_keeps_an_autosave_as_a_milestone(flow: Flow) -> None:
    await flow.run("idea")
    base = (await section(flow))["current_version"]["id"]
    auto = (await save(flow, "Draft in progress", autosave=True, base=base)).json()["version"]

    kept = await save(flow, "Draft in progress", base=auto["id"], note="Ready for supervisor")
    assert kept.status_code == 200 and kept.json()["version"]["id"] == auto["id"]
    assert kept.json()["version"]["change_note"] == "Ready for supervisor"

    # Typing afterwards starts a new version instead of merging into the milestone
    after = await save(flow, "Draft in progress, continued", autosave=True, base=auto["id"])
    assert after.status_code == 201 and after.json()["version"]["number"] == auto["number"] + 1


async def test_unchanged_content_creates_nothing(flow: Flow) -> None:
    await flow.run("idea")
    current = await section(flow)

    resp = await save(flow, current["content"], base=current["current_version"]["id"])

    assert resp.status_code == 200 and resp.json()["coalesced"] is True
    assert len(await versions(flow)) == 1


async def test_stale_base_version_is_a_conflict(flow: Flow) -> None:
    await flow.run("idea")
    v1 = (await section(flow))["current_version"]["id"]
    await save(flow, "Someone else's edit", base=v1)

    conflict = await save(flow, "My edit from an old tab", base=v1)

    assert conflict.status_code == 409 and "now v2" in conflict.json()["detail"]


# ─── Edit rules follow the stage ───────────────────────────────────────


async def test_editing_follows_the_stage(flow: Flow) -> None:
    assert (await save(flow, "too early")).status_code == 409

    await flow.run("idea")
    await flow.act("idea", "approve")
    approved = await section(flow)
    assert approved["status"] == "approved"
    assert approved["editable"] is False and approved["edit_blocked_reason"] == "Go back to this stage to edit it"
    blocked = await save(flow, "sneaky change", base=approved["current_version"]["id"])
    assert blocked.status_code == 409
    v1 = approved["current_version"]["id"]
    assert (
        await flow.client.post(flow.url(f"/sections/abstract/versions/{v1}/restore"), headers=flow.headers)
    ).status_code == 409

    await flow.act("idea", "go-back")
    reopened = await section(flow)
    assert reopened["editable"] is True and reopened["status"] == "draft"
    assert (await save(flow, "allowed now", base=reopened["current_version"]["id"])).status_code == 201


async def test_section_status_follows_stage_staleness(flow: Flow) -> None:
    await flow.run("idea")
    await flow.act("idea", "approve")
    await flow.run("literature")
    await flow.act("literature", "approve")
    assert (await section(flow, "literature_survey"))["status"] == "approved"

    await flow.act("idea", "go-back")

    assert (await section(flow, "literature_survey"))["status"] == "stale"


async def test_regenerate_and_review_edit_add_versions(flow: Flow) -> None:
    await flow.run("idea")
    await flow.act("idea", "regenerate", {"feedback": "shorter"}, expect=202)
    await flow.act("idea", "edit", {"content": "Edited in the review panel"})

    history = await versions(flow)
    assert [(v["number"], v["source"]) for v in history] == [(3, "ai_edited"), (2, "ai"), (1, "ai")]
    assert history[0]["change_note"] == "Edited in review"
    assert (await section(flow))["content"] == "Edited in the review panel"


async def test_sections_without_a_stage_are_always_editable(flow: Flow) -> None:
    resp = await save(flow, "GNN-Rank: Graph Neural Networks for Drug Candidate Ranking", kind="title")
    assert resp.status_code == 201 and resp.json()["version"]["source"] == "user"
    assert (await section(flow, "title"))["editable"] is True


# ─── Access ────────────────────────────────────────────────────────────


async def test_viewer_can_read_not_edit(flow: Flow, api_client: AsyncClient, db_session: AsyncSession) -> None:
    await flow.run("idea")
    bob = await auth_headers(api_client, "bob@example.com")
    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    db_session.add(ProjectMember(project_id=flow.pid, user_id=bob_id, role=MemberRole.VIEWER))
    await db_session.flush()

    seen = (await api_client.get(flow.url("/sections/abstract"), headers=bob)).json()
    assert seen["content"] and seen["editable"] is False and seen["edit_blocked_reason"] == "You have view-only access"
    resp = await api_client.post(flow.url("/sections/abstract/versions"), json={"content": "x"}, headers=bob)
    assert resp.status_code == 403


async def test_versions_are_scoped_to_their_section(flow: Flow) -> None:
    await flow.run("idea")
    v1 = (await section(flow))["current_version"]["id"]

    assert (
        await flow.client.get(flow.url(f"/sections/methodology/versions/{v1}"), headers=flow.headers)
    ).status_code == 404
    assert (
        await flow.client.get(flow.url(f"/sections/abstract/versions/{uuid.uuid4()}"), headers=flow.headers)
    ).status_code == 404
    assert (await flow.client.get(flow.url("/sections/not-a-section"), headers=flow.headers)).status_code == 422
