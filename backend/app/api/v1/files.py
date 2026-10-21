import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.core.config import Settings
from app.core.deps import CurrentUser, DbSession, SettingsDep
from app.core.logging import get_logger
from app.db.models import File, User
from app.db.models.enums import FileKind, MemberRole
from app.schemas.file import DownloadLink, FileRead
from app.services.files import SNIFF_BYTES, UploadRejectedError, validate_upload
from app.services.projects import get_project_for_user
from app.services.storage import Storage, get_storage, object_key

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])
log = get_logger(__name__)

StorageDep = Annotated[Storage, Depends(get_storage)]


async def _get_file(db: DbSession, project_id: uuid.UUID, file_id: uuid.UUID) -> File:
    file = await db.scalar(select(File).where(File.id == file_id, File.project_id == project_id))
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return file


@router.post("", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: uuid.UUID,
    file: UploadFile,
    kind: Annotated[FileKind, Form()],
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> File:
    await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    record = await save_upload(db, storage, settings, project_id, user, file, kind)
    await db.commit()
    return await _get_file(db, project_id, record.id)


async def save_upload(
    db: DbSession,
    storage: Storage,
    settings: Settings,
    project_id: uuid.UUID,
    user: User,
    file: UploadFile,
    kind: FileKind,
) -> File:
    """Validate and store an upload; the File row is flushed, the caller commits.

    If the caller's commit fails, the stored object is orphaned; callers commit right away.
    """
    size = file.size if file.size is not None else len(await file.read())
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {settings.max_upload_mb} MB limit")

    await file.seek(0)
    head = await file.read(SNIFF_BYTES)
    try:
        upload = validate_upload(kind, file.filename, head)
    except UploadRejectedError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    await file.seek(0)

    file_id = uuid.uuid4()
    key = object_key(project_id, file_id, upload.filename)
    await storage.put(key, file.file, size, upload.mime)

    record = File(
        id=file_id,
        project_id=project_id,
        kind=kind,
        filename=upload.filename,
        object_key=key,
        mime=upload.mime,
        size=size,
        uploaded_by=user.id,
    )
    db.add(record)
    try:
        await db.flush()
    except Exception:
        await storage.delete(key)  # don't leave an orphaned object behind
        raise
    log.info("file_uploaded", project_id=str(project_id), file_id=str(file_id), kind=kind.value, size=size)
    return record


@router.get("", response_model=list[FileRead])
async def list_files(
    project_id: uuid.UUID, user: CurrentUser, db: DbSession, kind: FileKind | None = None
) -> list[File]:
    await get_project_for_user(db, project_id, user)
    query = select(File).where(File.project_id == project_id).order_by(File.created_at.desc(), File.filename)
    if kind is not None:
        query = query.where(File.kind == kind)
    return list(await db.scalars(query))


@router.get("/{file_id}", response_model=FileRead)
async def get_file(project_id: uuid.UUID, file_id: uuid.UUID, user: CurrentUser, db: DbSession) -> File:
    await get_project_for_user(db, project_id, user)
    return await _get_file(db, project_id, file_id)


@router.get("/{file_id}/download", response_model=DownloadLink)
async def download_link(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> DownloadLink:
    """Short-lived presigned URL; the browser downloads straight from storage."""
    await get_project_for_user(db, project_id, user)
    file = await _get_file(db, project_id, file_id)
    expiry = settings.download_url_expiry_s
    url = storage.download_url(file.object_key, file.filename, timedelta(seconds=expiry))
    return DownloadLink(url=url, expires_in=expiry)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    project_id: uuid.UUID, file_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> Response:
    await get_project_for_user(db, project_id, user, min_role=MemberRole.EDITOR)
    file = await _get_file(db, project_id, file_id)
    key = file.object_key
    await db.delete(file)
    await db.commit()
    try:
        await storage.delete(key)
    except Exception:
        # The record is gone either way; a leftover object is only wasted space
        log.exception("file_object_delete_failed", object_key=key)
    log.info("file_deleted", project_id=str(project_id), file_id=str(file_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
