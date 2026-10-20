"""/projects CRUD, membership-based access, and isolation between users."""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, ResearchStateSnapshot, Stage, User
from app.db.models.enums import MemberRole, StageName

PASSWORD = "s3cret-passw0rd"


async def auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def alice(api_client: AsyncClient) -> dict[str, str]:
    return await auth_headers(api_client, "alice@example.com")


@pytest.fixture
async def bob(api_client: AsyncClient) -> dict[str, str]:
    return await auth_headers(api_client, "bob@example.com")


async def create(client: AsyncClient, headers: dict[str, str], **fields: str) -> dict[str, object]:
    body = {"title": "Graph neural networks for drug discovery", **fields}
    resp = await client.post("/api/v1/projects", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def test_create_returns_full_project(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession
) -> None:
    project = await create(
        api_client, alice, domain="Bioinformatics", raw_idea="Use GNNs to rank candidates", target_venue="ieee"
    )

    assert project["title"] == "Graph neural networks for drug discovery"
    assert project["domain"] == "Bioinformatics"
    assert project["raw_idea"] == "Use GNNs to rank candidates"
    assert project["target_venue"] == "ieee"
    assert project["citation_style"] == "ieee"
    assert project["current_stage"] == "idea"
    assert project["status"] == "active"
    assert project["role"] == "owner"
    # All 9 stages, in pipeline order, not started
    assert [s["name"] for s in project["stages"]] == [s.value for s in StageName]  # type: ignore[index]
    assert {s["status"] for s in project["stages"]} == {"not_started"}  # type: ignore[index]

    pid = uuid.UUID(str(project["id"]))
    member = await db_session.scalar(select(ProjectMember).where(ProjectMember.project_id == pid))
    assert member is not None and member.role == MemberRole.OWNER
    state = await db_session.get(ResearchStateSnapshot, pid)
    assert state is not None and state.snapshot["idea"]["raw_idea"] == "Use GNNs to rank candidates"


async def test_create_validates(api_client: AsyncClient, alice: dict[str, str]) -> None:
    for body in ({"title": ""}, {"title": "   "}, {}, {"title": "x" * 501}):
        resp = await api_client.post("/api/v1/projects", json=body, headers=alice)
        assert resp.status_code == 422, body


async def test_list_only_own_projects_newest_first(
    api_client: AsyncClient, alice: dict[str, str], bob: dict[str, str], db_session: AsyncSession
) -> None:
    first = await create(api_client, alice, title="First")
    second = await create(api_client, alice, title="Second")
    await create(api_client, bob, title="Bob's")
    # Everything in a test shares one transaction, so now() is identical; age the first project
    await db_session.execute(
        update(Project)
        .where(Project.id == uuid.UUID(str(first["id"])))
        .values(updated_at=func.now() - timedelta(hours=1))
    )

    resp = await api_client.get("/api/v1/projects", headers=alice)

    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()] == [second["id"], first["id"]]


async def test_update_fields_and_raw_idea(api_client: AsyncClient, alice: dict[str, str]) -> None:
    project = await create(api_client, alice, domain="Bio", raw_idea="old idea")

    resp = await api_client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"title": "Renamed", "raw_idea": "new idea", "target_venue": "acm", "domain": ""},
        headers=alice,
    )

    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["title"] == "Renamed"
    assert updated["raw_idea"] == "new idea"
    assert updated["target_venue"] == "acm"
    assert updated["domain"] is None  # blank clears the field
    assert updated["updated_at"] >= project["updated_at"]  # type: ignore[operator]

    unchanged = await api_client.patch(f"/api/v1/projects/{project['id']}", json={}, headers=alice)
    assert unchanged.json()["title"] == "Renamed"


async def test_update_rejects_null_title(api_client: AsyncClient, alice: dict[str, str]) -> None:
    project = await create(api_client, alice)

    resp = await api_client.patch(f"/api/v1/projects/{project['id']}", json={"title": None}, headers=alice)

    assert resp.status_code == 422


async def test_delete_removes_project_and_stages(
    api_client: AsyncClient, alice: dict[str, str], db_session: AsyncSession
) -> None:
    project = await create(api_client, alice)
    url = f"/api/v1/projects/{project['id']}"

    assert (await api_client.delete(url, headers=alice)).status_code == 204
    assert (await api_client.get(url, headers=alice)).status_code == 404
    pid = uuid.UUID(str(project["id"]))
    assert await db_session.scalar(select(func.count()).where(Stage.project_id == pid)) == 0


async def test_other_user_cannot_see_or_change_project(
    api_client: AsyncClient, alice: dict[str, str], bob: dict[str, str]
) -> None:
    project = await create(api_client, alice)
    url = f"/api/v1/projects/{project['id']}"

    assert (await api_client.get("/api/v1/projects", headers=bob)).json() == []
    # 404 (not 403): don't reveal that the project exists
    assert (await api_client.get(url, headers=bob)).status_code == 404
    assert (await api_client.patch(url, json={"title": "hijack"}, headers=bob)).status_code == 404
    assert (await api_client.delete(url, headers=bob)).status_code == 404
    assert (await api_client.get(url, headers=alice)).json()["title"] == project["title"]


async def test_member_roles(
    api_client: AsyncClient, alice: dict[str, str], bob: dict[str, str], db_session: AsyncSession
) -> None:
    project = await create(api_client, alice)
    url = f"/api/v1/projects/{project['id']}"
    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    membership = ProjectMember(project_id=uuid.UUID(str(project["id"])), user_id=bob_id, role=MemberRole.VIEWER)
    db_session.add(membership)
    await db_session.flush()

    # Viewer: can read, cannot edit or delete
    got = await api_client.get(url, headers=bob)
    assert got.status_code == 200 and got.json()["role"] == "viewer"
    assert (await api_client.patch(url, json={"title": "x"}, headers=bob)).status_code == 403
    assert (await api_client.delete(url, headers=bob)).status_code == 403

    # Editor: can edit, still cannot delete
    membership.role = MemberRole.EDITOR
    await db_session.flush()
    assert (await api_client.patch(url, json={"title": "Edited by Bob"}, headers=bob)).status_code == 200
    assert (await api_client.delete(url, headers=bob)).status_code == 403


async def test_requires_authentication(api_client: AsyncClient) -> None:
    assert (await api_client.get("/api/v1/projects")).status_code == 401
    assert (await api_client.post("/api/v1/projects", json={"title": "x"})).status_code == 401


async def test_invalid_project_id_is_422(api_client: AsyncClient, alice: dict[str, str]) -> None:
    assert (await api_client.get("/api/v1/projects/not-a-uuid", headers=alice)).status_code == 422
