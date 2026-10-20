"""Celery application. Start a worker with:

celery -A app.workers.celery_app worker --loglevel=INFO
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()

celery_app = Celery(
    "researchflow",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Long-running tasks: take one at a time, and only acknowledge once finished
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=24 * 3600,
    broker_connection_retry_on_startup=True,
)


@setup_logging.connect
def _use_app_logging(**_: Any) -> None:
    """Keep Celery from replacing our structured logging setup."""
    configure_logging(get_settings())
