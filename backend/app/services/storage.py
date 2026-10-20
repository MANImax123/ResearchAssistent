"""Object storage (MinIO / S3). The only module that talks to MinIO for project files.

The MinIO SDK is synchronous, so calls run in a worker thread.
"""

import asyncio
import re
import uuid
from datetime import timedelta
from functools import lru_cache
from typing import BinaryIO
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings, get_settings

# Signing a URL is a local computation, but the SDK looks up the bucket region over the
# network unless it's given; fixing it lets us sign for a host the backend can't reach.
REGION = "us-east-1"


class Storage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
        public_endpoint: str | None = None,
        public_secure: bool | None = None,
    ) -> None:
        self.bucket = bucket
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure, region=REGION)
        self._public = Minio(
            public_endpoint or endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure if public_secure is None else public_secure,
            region=REGION,
        )

    @classmethod
    def from_settings(cls, s: Settings) -> "Storage":
        return cls(
            s.minio_endpoint,
            s.minio_access_key,
            s.minio_secret_key,
            s.minio_bucket,
            secure=s.minio_secure,
            public_endpoint=s.minio_public_endpoint or None,
            public_secure=s.minio_public_secure,
        )

    async def ensure_bucket(self) -> None:
        if not await asyncio.to_thread(self._client.bucket_exists, self.bucket):
            await asyncio.to_thread(self._client.make_bucket, self.bucket)

    async def put(self, key: str, data: BinaryIO, size: int, content_type: str) -> None:
        await asyncio.to_thread(self._client.put_object, self.bucket, key, data, size, content_type=content_type)

    async def get_bytes(self, key: str) -> bytes:
        def read() -> bytes:
            resp = self._client.get_object(self.bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(read)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.stat_object, self.bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self.bucket, key)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under `prefix`; returns how many were removed."""

        def run() -> int:
            keys = [o.object_name for o in self._client.list_objects(self.bucket, prefix=prefix, recursive=True)]
            for key in keys:
                if key:
                    self._client.remove_object(self.bucket, key)
            return len(keys)

        return await asyncio.to_thread(run)

    def download_url(self, key: str, filename: str, expires: timedelta) -> str:
        """Presigned GET URL that makes the browser save the file under its original name."""
        disposition = f"attachment; filename=\"{_ascii_fallback(filename)}\"; filename*=UTF-8''{quote(filename)}"
        return self._public.presigned_get_object(
            self.bucket,
            key,
            expires=expires,
            response_headers={"response-content-disposition": disposition},
        )


def object_key(project_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> str:
    return f"projects/{project_id}/files/{file_id}/{filename}"


def project_prefix(project_id: uuid.UUID) -> str:
    return f"projects/{project_id}/"


def _ascii_fallback(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", filename)


@lru_cache
def get_storage() -> Storage:
    return Storage.from_settings(get_settings())
