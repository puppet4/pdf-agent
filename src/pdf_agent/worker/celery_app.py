"""Celery application configuration."""
from __future__ import annotations

from celery import Celery

from pdf_agent.config import settings

celery_app = Celery(
    "pdf_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # One task at a time for heavy PDF ops
    task_soft_time_limit=settings.external_cmd_timeout_sec,
    task_time_limit=settings.external_cmd_timeout_sec + 60,
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["pdf_agent.worker"])
