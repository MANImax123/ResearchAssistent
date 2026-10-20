import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models.enums import FileKind


class FileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: FileKind
    filename: str
    mime: str
    size: int
    uploaded_by: uuid.UUID | None
    created_at: datetime


class DownloadLink(BaseModel):
    url: str
    expires_in: int  # seconds
