"""Celery tasks. One generic task runs any registered job handler."""

import asyncio
import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.models.enums import JobStatus
from app.services.jobs import SessionFactory, execute_job
from app.workers import db
from app.workers.celery_app import celery_app
from app.workers.handlers import HANDLERS

log = get_logger(__name__)

# Tests point this at the test database
session_factory: SessionFactory = db.worker_session


@celery_app.task(name="jobs.run")
def run_job(job_id: str, job_type: str, params: dict[str, Any]) -> str:
    handler = HANDLERS.get(job_type)
    if handler is None:
        raise ValueError(f"Unknown job type: {job_type}")
    log.info("job_started", job_id=job_id, job_type=job_type)
    # Celery tasks are synchronous; each runs its async work on a fresh event loop
    status: JobStatus = asyncio.run(execute_job(uuid.UUID(job_id), handler, params, session_factory))
    return status.value
