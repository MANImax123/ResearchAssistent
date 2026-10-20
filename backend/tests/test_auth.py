"""Auth flow: register → login → me, and every way a request should get a 401."""

import uuid

from httpx import AsyncClient
from jose import jwt
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import JWT_ALGORITHM, create_access_token
from app.db.models import User

PASSWORD = "s3cret-passw0rd"


async def register(client: AsyncClient, email: str = "Alice@Example.com", name: str = "Alice") -> dict[str, str]:
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> str:
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]  # type: ignore[no-any-return]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_register_login_me(api_client: AsyncClient) -> None:
    user = await register(api_client)
    assert user["email"] == "alice@example.com"  # normalised
    assert "password" not in user and "password_hash" not in user

    login_resp = await api_client.post(
        "/api/v1/auth/login", data={"username": "ALICE@example.com", "password": PASSWORD}
    )
    assert login_resp.status_code == 200
    body = login_resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().jwt_expire_minutes * 60

    me = await api_client.get("/api/v1/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    assert me.json()["name"] == "Alice"


async def test_register_duplicate_email_conflicts(api_client: AsyncClient) -> None:
    await register(api_client, "bob@example.com")

    resp = await api_client.post(
        "/api/v1/auth/register", json={"email": "BOB@example.com", "password": PASSWORD, "name": "Bob 2"}
    )
    assert resp.status_code == 409


async def test_register_validates_input(api_client: AsyncClient) -> None:
    bad_email = await api_client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD, "name": "X"}
    )
    short_password = await api_client.post(
        "/api/v1/auth/register", json={"email": "x@example.com", "password": "short", "name": "X"}
    )
    assert bad_email.status_code == 422
    assert short_password.status_code == 422


async def test_login_wrong_password_401(api_client: AsyncClient) -> None:
    await register(api_client, "carol@example.com")

    wrong = await api_client.post("/api/v1/auth/login", data={"username": "carol@example.com", "password": "nope-nope"})
    unknown = await api_client.post("/api/v1/auth/login", data={"username": "nobody@example.com", "password": PASSWORD})

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    # Same message either way: don't reveal which emails are registered
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_me_without_token_401(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/auth/me")

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_bad_tokens(api_client: AsyncClient) -> None:
    settings = get_settings()
    user = await register(api_client, "dave@example.com")
    user_id = uuid.UUID(user["id"])

    expired = create_access_token(user_id, settings.model_copy(update={"jwt_expire_minutes": -1}))
    wrong_key = create_access_token(user_id, settings.model_copy(update={"secret_key": "another-key"}))
    not_access = jwt.encode({"sub": str(user_id), "type": "refresh"}, settings.secret_key, algorithm=JWT_ALGORITHM)
    unsigned = jwt.encode({"sub": str(user_id), "type": "access"}, "", algorithm=JWT_ALGORITHM)

    for token in ("garbage", expired, wrong_key, not_access, unsigned):
        resp = await api_client.get("/api/v1/auth/me", headers=bearer(token))
        assert resp.status_code == 401, token


async def test_token_for_deleted_user_401(api_client: AsyncClient, db_session: AsyncSession) -> None:
    await register(api_client, "erin@example.com")
    token = await login(api_client, "erin@example.com")

    await db_session.execute(delete(User).where(User.email == "erin@example.com"))
    await db_session.flush()

    resp = await api_client.get("/api/v1/auth/me", headers=bearer(token))
    assert resp.status_code == 401
