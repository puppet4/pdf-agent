"""Celery worker entrypoint for queue-backed execution runs."""
from __future__ import annotations

try:  # pragma: no cover - optional runtime dependency
    from celery.signals import worker_process_init, worker_ready
except Exception:  # pragma: no cover
    worker_process_init = None
    worker_ready = None

from pdf_agent.execution_queue import get_celery_app, record_worker_heartbeat
from pdf_agent.api.executions import run_execution_sync

celery_app = get_celery_app()

if celery_app is not None and worker_ready is not None and worker_process_init is not None:  # pragma: no branch
    @worker_ready.connect
    def _on_worker_ready(**kwargs):
        record_worker_heartbeat("worker-ready")

    @worker_process_init.connect
    def _on_worker_process_init(**kwargs):
        record_worker_heartbeat("worker-process")

    @celery_app.task(name="pdf_agent.run_execution")
    def run_execution_task(execution_id: str):
        record_worker_heartbeat("worker-task")
        run_execution_sync(execution_id)
