"""Celery tasks for job execution."""
from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pdf_agent.config import settings
from pdf_agent.tools.registry import load_builtin_tools
from pdf_agent.worker.celery_app import celery_app
from pdf_agent.worker.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# Sync engine for Celery worker (Celery is sync)
_sync_db_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace("postgresql+asyncpg", "postgresql")
_sync_engine = create_engine(_sync_db_url, pool_pre_ping=True)

# Ensure tools are loaded in worker process
load_builtin_tools()


@celery_app.task(bind=True, name="pdf_agent.worker.tasks.execute_job")
def execute_job(self, job_id: str, input_file_paths: list[str]) -> dict:
    """Execute a job plan."""
    import uuid

    logger.info("Starting job execution: %s", job_id)
    with Session(_sync_engine) as session:
        orchestrator = Orchestrator(session)
        orchestrator.execute_job(uuid.UUID(job_id), input_file_paths)
    return {"job_id": job_id, "status": "dispatched"}
