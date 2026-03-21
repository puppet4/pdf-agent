"""Execution queue abstraction with optional Celery/Redis backend."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.external_commands import cancel_job_processes

try:  # pragma: no cover - optional runtime dependency
    from celery import Celery
except Exception:  # pragma: no cover
    Celery = None

_local_tasks: dict[str, asyncio.Task[None]] = {}
_celery_task_ids: dict[str, str] = {}
_queued_counts: dict[str, int] = {"light": 0, "heavy": 0}


def get_celery_app():
    if not settings.celery_enabled or Celery is None:
        return None
    app = Celery(
        "pdf_agent",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_default_queue="pdf_executions_light",
        task_always_eager=settings.celery_task_always_eager,
        task_track_started=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
    )
    return app


def get_worker_state() -> dict[str, object]:
    heartbeat_file = settings.worker_dir / "heartbeat.json"
    worker_status = "not_configured"
    last_seen = None
    if settings.celery_enabled and Celery is not None:
        worker_status = "unknown"
    if heartbeat_file.exists():
        payload = json.loads(heartbeat_file.read_text(encoding="utf-8"))
        worker_status = "alive"
        last_seen = payload.get("timestamp")
    return {
        "backend": "celery" if settings.celery_enabled and Celery is not None else "local",
        "worker_status": worker_status,
        "last_heartbeat": last_seen,
        "queues": dict(_queued_counts),
    }


def record_worker_heartbeat(worker_name: str = "worker") -> None:
    settings.worker_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker": worker_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (settings.worker_dir / "heartbeat.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def enqueue_execution(
    *,
    execution_id: str,
    queue_name: str,
    local_runner: Callable[[str], Awaitable[None]],
) -> dict[str, str]:
    _queued_counts[queue_name] = _queued_counts.get(queue_name, 0) + 1
    metrics.set_queue_length(queue_name, _queued_counts[queue_name])

    celery_app = get_celery_app()
    if celery_app is not None and not settings.celery_task_always_eager:
        try:
            task = celery_app.send_task(
                "pdf_agent.run_execution",
                args=[execution_id],
                queue=_queue_to_celery_name(queue_name),
            )
            _celery_task_ids[execution_id] = task.id
            return {"backend": "celery", "task_id": task.id, "queue": queue_name}
        except Exception:
            pass

    task = asyncio.create_task(local_runner(execution_id))
    _local_tasks[execution_id] = task
    task.add_done_callback(lambda _: _local_tasks.pop(execution_id, None))
    return {"backend": "local", "task_id": execution_id, "queue": queue_name}


def cancel_enqueued_execution(execution_id: str) -> dict[str, object]:
    terminated = cancel_job_processes(execution_id)

    task = _local_tasks.pop(execution_id, None)
    if task is not None:
        task.cancel()

    celery_task_id = _celery_task_ids.pop(execution_id, None)
    celery_app = get_celery_app()
    if celery_task_id and celery_app is not None:
        celery_app.control.revoke(celery_task_id, terminate=True)

    return {
        "terminated_processes": terminated,
        "celery_task_id": celery_task_id,
    }


def discard_queued_execution(queue_name: str) -> None:
    _queued_counts[queue_name] = max(0, _queued_counts.get(queue_name, 0) - 1)
    metrics.set_queue_length(queue_name, _queued_counts[queue_name])


def mark_execution_started(queue_name: str) -> None:
    discard_queued_execution(queue_name)
    record_worker_heartbeat()


def _queue_to_celery_name(queue_name: str) -> str:
    return "pdf_executions_heavy" if queue_name == "heavy" else "pdf_executions_light"
