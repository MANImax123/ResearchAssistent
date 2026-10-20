"""File uploads against real MinIO (test bucket): roundtrip, validation, access, cleanup."""

import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import File, ProjectMember, User
from app.db.models.enums import FileKind, MemberRole
from app.services.files import UploadRejectedError, safe_filename, validate_upload
from app.services.storage import Storage
from tests.conftest import TEST_SETTINGS

PASSWORD = "s3cret-passw0rd"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + bytes(17)
CSV = b"model,accuracy,f1\nbaseline,0.81,0.79\nours,0.87,0.85\n"


async def auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "name": email})
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def alice(api_client: AsyncClient) -> dict[str, str]:
    return await auth_headers(api_client, "alice@example.com")


@pytest.fixture
async def project_id(api_client: AsyncClient, alice: dict[str, str]) -> str:
    resp = await api_client.post("/api/v1/projects", json={"title": "Files test"}, headers=alice)
    return str(resp.json()["id"])


async def upload(
    client: AsyncClient, headers: dict[str, str], project_id: str, name: str, data: bytes, kind: str
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/files",
        files={"file": (name, data, "application/octet-stream")},
        data={"kind": kind},
        headers=headers,
    )


async def test_upload_list_download_delete_roundtrip(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage
) -> None:
    base = f"/api/v1/projects/{project_id}/files"
    uploads = {
        "paper.pdf": (PDF, "paper_pdf", "application/pdf"),
        "results.csv": (CSV, "result", "text/csv"),
        "architecture.png": (PNG, "diagram", "image/png"),
    }
    ids = {}
    for name, (data, kind, mime) in uploads.items():
        resp = await upload(api_client, alice, project_id, name, data, kind)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert (body["filename"], body["kind"], body["mime"], body["size"]) == (name, kind, mime, len(data))
        ids[name] = body["id"]

    listed = (await api_client.get(base, headers=alice)).json()
    assert {f["filename"] for f in listed} == set(uploads)
    only_diagrams = (await api_client.get(f"{base}?kind=diagram", headers=alice)).json()
    assert [f["filename"] for f in only_diagrams] == ["architecture.png"]

    # Each file downloads byte-for-byte via its presigned URL, as an attachment with the original name
    for name, (data, _, _) in uploads.items():
        link = (await api_client.get(f"{base}/{ids[name]}/download", headers=alice)).json()
        assert link["expires_in"] == get_settings().download_url_expiry_s
        async with httpx.AsyncClient() as raw:
            resp = await raw.get(link["url"])
        assert resp.status_code == 200
        assert resp.content == data
        assert f'filename="{name}"' in resp.headers["content-disposition"]
        assert resp.headers["content-disposition"].startswith("attachment")

    # Delete one: record and object both gone, the others remain
    record = (await api_client.get(f"{base}/{ids['results.csv']}", headers=alice)).json()
    assert (await api_client.delete(f"{base}/{ids['results.csv']}", headers=alice)).status_code == 204
    assert (await api_client.get(f"{base}/{ids['results.csv']}", headers=alice)).status_code == 404
    assert {f["filename"] for f in (await api_client.get(base, headers=alice)).json()} == {
        "paper.pdf",
        "architecture.png",
    }
    key = f"projects/{project_id}/files/{record['id']}/results.csv"
    assert not await storage.exists(key)


async def test_rejects_disallowed_extension_for_kind(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage
) -> None:
    exe = await upload(api_client, alice, project_id, "tool.exe", b"MZ\x90\x00", "code")
    csv_as_paper = await upload(api_client, alice, project_id, "data.csv", CSV, "paper_pdf")

    assert exe.status_code == 415
    assert csv_as_paper.status_code == 415
    assert "allowed: .pdf" in csv_as_paper.json()["detail"]


async def test_rejects_content_that_does_not_match_extension(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage
) -> None:
    fake_pdf = await upload(api_client, alice, project_id, "paper.pdf", b"<html>not a pdf</html>", "paper_pdf")
    binary_csv = await upload(api_client, alice, project_id, "data.csv", b"\x00\x01\x02binary", "dataset")
    empty = await upload(api_client, alice, project_id, "empty.txt", b"", "notes")

    assert fake_pdf.status_code == 422
    assert binary_csv.status_code == 422
    assert empty.status_code == 422
    assert (await api_client.get(f"/api/v1/projects/{project_id}/files", headers=alice)).json() == []


async def test_rejects_unknown_kind(api_client: AsyncClient, alice: dict[str, str], project_id: str) -> None:
    resp = await upload(api_client, alice, project_id, "paper.pdf", PDF, "malware")
    assert resp.status_code == 422


async def test_rejects_oversized_file(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage
) -> None:
    from app.core.config import get_settings as settings_dep
    from app.main import app

    app.dependency_overrides[settings_dep] = lambda: TEST_SETTINGS.model_copy(update={"max_upload_mb": 1})
    try:
        resp = await upload(api_client, alice, project_id, "big.csv", b"a,b\n" * 300_000, "dataset")
    finally:
        app.dependency_overrides.pop(settings_dep, None)

    assert resp.status_code == 413


async def test_filename_is_sanitised(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage
) -> None:
    # Path traversal is stripped; control characters are covered by test_safe_filename
    # (HTTP clients percent-encode them in multipart filenames before they reach us)
    resp = await upload(api_client, alice, project_id, "../../etc/passwd.csv", CSV, "dataset")

    assert resp.status_code == 201
    assert resp.json()["filename"] == "passwd.csv"


async def test_access_control(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage, db_session: AsyncSession
) -> None:
    file_id = (await upload(api_client, alice, project_id, "paper.pdf", PDF, "paper_pdf")).json()["id"]
    base = f"/api/v1/projects/{project_id}/files"
    bob = await auth_headers(api_client, "bob@example.com")

    # Non-member: everything is 404
    assert (await api_client.get(base, headers=bob)).status_code == 404
    assert (await api_client.get(f"{base}/{file_id}/download", headers=bob)).status_code == 404
    assert (await upload(api_client, bob, project_id, "x.pdf", PDF, "paper_pdf")).status_code == 404
    assert (await api_client.delete(f"{base}/{file_id}", headers=bob)).status_code == 404

    # Viewer: can list and download, cannot upload or delete
    bob_id = await db_session.scalar(select(User.id).where(User.email == "bob@example.com"))
    db_session.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=bob_id, role=MemberRole.VIEWER))
    await db_session.flush()
    assert len((await api_client.get(base, headers=bob)).json()) == 1
    assert (await api_client.get(f"{base}/{file_id}/download", headers=bob)).status_code == 200
    assert (await upload(api_client, bob, project_id, "x.pdf", PDF, "paper_pdf")).status_code == 403
    assert (await api_client.delete(f"{base}/{file_id}", headers=bob)).status_code == 403

    # A file id from another project is not reachable through this project
    other = (await api_client.post("/api/v1/projects", json={"title": "Other"}, headers=alice)).json()["id"]
    assert (await api_client.get(f"/api/v1/projects/{other}/files/{file_id}", headers=alice)).status_code == 404


async def test_deleting_project_removes_its_objects(
    api_client: AsyncClient, alice: dict[str, str], project_id: str, storage: Storage, db_session: AsyncSession
) -> None:
    await upload(api_client, alice, project_id, "paper.pdf", PDF, "paper_pdf")
    key = await db_session.scalar(select(File.object_key).where(File.project_id == uuid.UUID(project_id)))
    assert key and await storage.exists(key)

    assert (await api_client.delete(f"/api/v1/projects/{project_id}", headers=alice)).status_code == 204

    assert not await storage.exists(key)


def test_presigned_url_is_signed_for_public_host() -> None:
    public = Storage("minio:9000", "k", "s", "bucket", public_endpoint="localhost:9000")
    url = public.download_url("projects/p/files/f/résumé.pdf", "résumé.pdf", timedelta(minutes=5))

    parsed = urlparse(url)
    assert parsed.netloc == "localhost:9000"
    disposition = parse_qs(parsed.query)["response-content-disposition"][0]
    assert disposition == "attachment; filename=\"r_sum_.pdf\"; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("paper.pdf", "paper.pdf"),
        ("C:\\Users\\me\\paper.pdf", "paper.pdf"),
        ("../../secret.csv", "secret.csv"),
        ("pass\x00wd\x1f.csv", "pass_wd_.csv"),
        ('what?:*"<>|.txt', "what_______.txt"),
        ("  .hidden  ", "hidden"),
        ("", "file"),
        ("a" * 300 + ".pdf", "a" * 196 + ".pdf"),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_validate_upload_uses_extension_for_mime() -> None:
    result = validate_upload(FileKind.DATASET, "Data.CSV", CSV)
    assert (result.filename, result.extension, result.mime) == ("Data.CSV", ".csv", "text/csv")

    with pytest.raises(UploadRejectedError) as exc:
        validate_upload(FileKind.DIAGRAM, "diagram.png", PDF)
    assert exc.value.status_code == 422
