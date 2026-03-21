"""Execution API - thin execution record layer for tool, workflow, and agent runs."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import mimetypes
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from pdf_agent.agent.tools_adapter import invoke_adapted_tool
from pdf_agent.api.metrics import metrics
from pdf_agent.config import settings
from pdf_agent.core import PDFAgentError
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import ExecutionRecord, FileRecord
from pdf_agent.execution_queue import (
    _local_tasks as _execution_tasks,
    cancel_enqueued_execution,
    discard_queued_execution,
    enqueue_execution,
    mark_execution_started,
)
from pdf_agent.tools.registry import registry

router = APIRouter(prefix="/api/executions", tags=["executions"])
logger = logging.getLogger(__name__)

_TERMINAL_EXECUTION_STATUSES = {"SUCCESS", "FAILED", "CANCELED"}
_execution_event_buffers: dict[str, list[str]] = {}
_execution_event_queues: dict[str, list[asyncio.Queue[str]]] = {}


class PlanInputRef(BaseModel):
    type: str
    file_id: str | None = None


class ExecutionStepRequest(BaseModel):
    tool: str
    inputs: list[PlanInputRef] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class CreateExecutionRequest(BaseModel):
    mode: str
    file_ids: list[str] = Field(default_factory=list)
    steps: list[ExecutionStepRequest]
    instruction: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ExecutionFileResponse:
    path: str
    filename: str
    media_type: str | None = None


async def create_execution_record(
    *,
    mode: str,
    instruction: str | None,
    steps: list[dict[str, Any]],
    file_ids: list[str],
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_mode = (mode or "").upper()
    if normalized_mode not in {"FORM", "AGENT"}:
        raise HTTPException(status_code=422, detail="mode must be FORM or AGENT")
    if not steps:
        raise HTTPException(status_code=422, detail="steps must not be empty")

    plan = _normalize_plan(steps=steps, file_ids=file_ids, output=output or {})
    await _validate_plan_inputs(plan)

    queue_name = _queue_name_for_plan(plan)
    plan["queue_name"] = queue_name

    async with async_session_factory() as session:
        execution = ExecutionRecord(
            status="PENDING",
            mode=normalized_mode,
            instruction=instruction,
            plan_json=plan,
            progress_int=0,
            logs_json=[],
            outputs_json=[],
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        created = _serialize_execution(execution)

    _emit_execution_event(str(execution.id), "created", {"execution_id": str(execution.id), "status": "PENDING", "queue": queue_name})
    queue_info = enqueue_execution(execution_id=str(execution.id), queue_name=queue_name, local_runner=run_execution)
    created.update({"queue": queue_info["queue"], "queue_backend": queue_info["backend"]})
    return created


async def get_execution_record(execution_id: str) -> dict[str, Any]:
    async with async_session_factory() as session:
        execution = await _load_execution(session, execution_id)
        return _serialize_execution(execution)


async def list_execution_records(limit: int = 100) -> dict[str, Any]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(ExecutionRecord)
            .order_by(ExecutionRecord.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        executions = result.scalars().all()
    return {"executions": [_serialize_execution(execution) for execution in executions], "count": len(executions)}


async def cancel_execution_record(execution_id: str) -> dict[str, Any]:
    async with async_session_factory() as session:
        execution = await _load_execution(session, execution_id)
        if execution.status in _TERMINAL_EXECUTION_STATUSES:
            return _serialize_execution(execution)

        queue_name = str((execution.plan_json or {}).get("queue_name", "light"))
        if execution.status == "PENDING":
            discard_queued_execution(queue_name)

        if execution.status not in _TERMINAL_EXECUTION_STATUSES:
            execution.status = "CANCELED"
            execution.error_code = "EXECUTION_CANCELED"
            execution.error_message = "Execution canceled by user"
            await session.commit()
        cancel_info = cancel_enqueued_execution(execution_id)
        payload = _serialize_execution(execution)
    _emit_execution_event(str(execution.id), "done", {"execution_id": str(execution.id), "status": "CANCELED"})
    return {**payload, **cancel_info}


async def get_execution_result_response(execution_id: str) -> ExecutionFileResponse:
    async with async_session_factory() as session:
        execution = await _load_execution(session, execution_id)
    if not execution.result_path:
        raise HTTPException(status_code=404, detail="Execution result not found")
    return _build_file_response(Path(execution.result_path))


async def get_execution_output_response(execution_id: str, filename: str) -> ExecutionFileResponse:
    async with async_session_factory() as session:
        execution = await _load_execution(session, execution_id)
    output_root = settings.executions_dir / execution_id / "output"
    candidate = (output_root / filename).resolve()
    try:
        candidate.relative_to(output_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid output path") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    if candidate.name not in {item.get("filename") for item in execution.outputs_json or []}:
        raise HTTPException(status_code=404, detail="Output file not found")
    return _build_file_response(candidate)


async def stream_execution_events(execution_id: str) -> AsyncIterator[str]:
    queue: asyncio.Queue[str] = asyncio.Queue()
    listeners = _execution_event_queues.setdefault(execution_id, [])
    listeners.append(queue)
    try:
        for item in _execution_event_buffers.get(execution_id, []):
            yield item
        while True:
            message = await queue.get()
            yield message
            if message.startswith("event: done"):
                break
    finally:
        listeners = _execution_event_queues.get(execution_id, [])
        if queue in listeners:
            listeners.remove(queue)
        if not listeners:
            _execution_event_queues.pop(execution_id, None)


async def wait_for_execution_terminal(execution_id: str, timeout: float | None = None) -> dict[str, Any]:
    deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
    while True:
        execution = await get_execution_record(execution_id)
        if execution["status"] in _TERMINAL_EXECUTION_STATUSES:
            return execution
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(status_code=504, detail="Execution timed out")
        await asyncio.sleep(0.05)


@router.post("", status_code=201)
async def create_execution(req: CreateExecutionRequest):
    kwargs = {
        "mode": req.mode,
        "instruction": req.instruction,
        "steps": [
            {key: value for key, value in step.model_dump().items() if key != "inputs" or value}
            for step in req.steps
        ],
        "file_ids": req.file_ids,
    }
    if req.output:
        kwargs["output"] = req.output
    return await create_execution_record(**kwargs)


@router.get("")
async def list_executions(limit: int = 100):
    return await list_execution_records(limit=limit)


@router.get("/{execution_id}")
async def get_execution(execution_id: str):
    return await get_execution_record(execution_id)


@router.post("/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    return await cancel_execution_record(execution_id)


@router.get("/{execution_id}/result")
async def get_execution_result(execution_id: str):
    result = await get_execution_result_response(execution_id)
    return FileResponse(result.path, filename=result.filename, media_type=result.media_type)


@router.get("/{execution_id}/outputs/{filename:path}")
async def get_execution_output(execution_id: str, filename: str):
    result = await get_execution_output_response(execution_id, filename)
    return FileResponse(result.path, filename=result.filename, media_type=result.media_type)


@router.get("/{execution_id}/events")
async def execution_events(execution_id: str):
    return StreamingResponse(stream_execution_events(execution_id), media_type="text/event-stream")


async def run_execution(execution_id: str) -> None:
    await _run_execution(execution_id)


def run_execution_sync(execution_id: str) -> None:
    asyncio.run(run_execution(execution_id))


def _normalize_plan(*, steps: list[dict[str, Any]], file_ids: list[str], output: dict[str, Any]) -> dict[str, Any]:
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        raw_inputs = step.get("inputs") or []
        normalized_inputs = raw_inputs
        if not normalized_inputs:
            if index == 0 and file_ids:
                normalized_inputs = [{"type": "file", "file_id": file_id} for file_id in file_ids]
            else:
                normalized_inputs = [{"type": "prev"}]
        normalized_steps.append(
            {
                "tool": str(step.get("tool", "")),
                "inputs": normalized_inputs,
                "params": step.get("params", {}) if isinstance(step.get("params", {}), dict) else {},
            }
        )
    return {"version": "1.0", "steps": normalized_steps, "output": output or {}}


async def _validate_plan_inputs(plan: dict[str, Any]) -> None:
    file_ids: list[str] = []
    for step in plan.get("steps", []):
        if not step.get("tool"):
            raise HTTPException(status_code=422, detail="step tool must not be empty")
        for input_ref in step.get("inputs", []):
            if input_ref.get("type") == "file":
                raw_id = input_ref.get("file_id")
                if not raw_id:
                    raise HTTPException(status_code=422, detail="file input requires file_id")
                file_ids.append(raw_id)
            elif input_ref.get("type") != "prev":
                raise HTTPException(status_code=422, detail=f"Unsupported input type: {input_ref.get('type')}")
    await _validate_file_ids(file_ids)


def _queue_name_for_plan(plan: dict[str, Any]) -> str:
    for step in plan.get("steps", []):
        tool = registry.get(step.get("tool", ""))
        if tool is not None and tool.manifest().async_hint:
            return "heavy"
    return "light"


async def _validate_file_ids(file_ids: list[str]) -> None:
    if not file_ids:
        return
    parsed_ids: list[uuid.UUID] = []
    for raw_id in file_ids:
        try:
            parsed_ids.append(uuid.UUID(raw_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid file_id: {raw_id}") from exc
    async with async_session_factory() as session:
        result = await session.execute(select(FileRecord.id).where(FileRecord.id.in_(parsed_ids)))
        found = {str(file_id) for file_id in result.scalars().all()}
    missing = [file_id for file_id in file_ids if file_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"File {missing[0]} not found")


async def _load_execution(session, execution_id: str) -> ExecutionRecord:
    try:
        parsed_id = uuid.UUID(execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid execution_id") from exc

    if hasattr(session, "execute"):
        result = await session.execute(select(ExecutionRecord).where(ExecutionRecord.id == parsed_id))
        execution = result.scalar_one_or_none()
    else:
        execution = await session.get(ExecutionRecord, parsed_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution


def _serialize_execution(execution: ExecutionRecord) -> dict[str, Any]:
    outputs = []
    for item in execution.outputs_json or []:
        filename = str(item.get("filename") or "")
        outputs.append(
            {
                **item,
                "filename": filename,
                "download_url": f"/api/executions/{execution.id}/outputs/{filename}" if filename else None,
            }
        )
    return {
        "id": str(execution.id),
        "status": execution.status,
        "mode": execution.mode,
        "instruction": execution.instruction,
        "progress_int": execution.progress_int,
        "active_tool": execution.active_tool,
        "error_code": execution.error_code,
        "error_message": execution.error_message,
        "result_path": execution.result_path,
        "result_type": execution.result_type,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
        "plan": execution.plan_json or {},
        "logs": execution.logs_json or [],
        "outputs": outputs,
    }


def _format_sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _emit_execution_event(execution_id: str, event: str, payload: dict[str, Any]) -> None:
    message = _format_sse_event(event, payload)
    _execution_event_buffers.setdefault(execution_id, []).append(message)
    for queue in _execution_event_queues.get(execution_id, []):
        queue.put_nowait(message)


async def _run_execution(execution_id: str) -> None:
    async with async_session_factory() as session:
        execution = await _load_execution(session, execution_id)
        if execution.status == "CANCELED":
            return

        plan = execution.plan_json or {}
        queue_name = str(plan.get("queue_name", "light"))
        mark_execution_started(queue_name)

        execution.status = "RUNNING"
        execution.logs_json = execution.logs_json or []
        await session.commit()
        _emit_execution_event(execution_id, "progress", {"execution_id": execution_id, "status": "RUNNING", "progress_int": 0})

        execution_root = settings.executions_dir / execution_id
        work_root = execution_root / "work"
        output_root = execution_root / "output"
        work_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)

        prev_outputs: list[Path] = []
        step_plans = plan.get("steps", [])
        total_steps = max(1, len(step_plans))

        try:
            for index, step_plan in enumerate(step_plans, start=1):
                await session.refresh(execution)
                if execution.status == "CANCELED":
                    break

                tool_name = str(step_plan.get("tool", ""))
                tool = registry.get(tool_name)
                if tool is None:
                    raise HTTPException(status_code=500, detail=f"Tool '{tool_name}' not found")

                step_inputs = await _resolve_step_inputs(session, step_plan.get("inputs", []), prev_outputs)
                logs = list(execution.logs_json or [])
                log_entry = {
                    "index": index - 1,
                    "tool": tool_name,
                    "status": "RUNNING",
                    "params": step_plan.get("params", {}) if isinstance(step_plan.get("params"), dict) else {},
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "ended_at": None,
                    "log_text": "",
                    "output_files": [],
                }
                logs.append(log_entry)
                execution.active_tool = tool_name
                execution.logs_json = logs
                await session.commit()
                _emit_execution_event(
                    execution_id,
                    "step",
                    {"execution_id": execution_id, "step": index - 1, "tool": tool_name, "status": "RUNNING"},
                )

                def reporter(percent: int, message: str = "") -> None:
                    base = (index - 1) / total_steps * 100
                    step_share = max(0.0, min(100.0, percent)) / total_steps
                    current = int(min(99, base + step_share))
                    _emit_execution_event(
                        execution_id,
                        "progress",
                        {
                            "execution_id": execution_id,
                            "status": "RUNNING",
                            "progress_int": current,
                            "step": index - 1,
                            "tool": tool_name,
                            "message": message,
                        },
                    )

                result = await invoke_adapted_tool(
                    registry=registry,
                    tool_name=tool_name,
                    input_paths=step_inputs,
                    params=step_plan.get("params", {}) if isinstance(step_plan.get("params"), dict) else {},
                    thread_workdir=work_root,
                    step_counter=index - 1,
                    thread_id=execution_id,
                    progress_reporter=reporter,
                )

                is_final_step = index == total_steps
                stored_outputs = _store_step_outputs(
                    output_files=[Path(path) for path in result.output_files],
                    output_root=output_root,
                    is_final_step=is_final_step,
                )
                prev_outputs = stored_outputs or step_inputs

                logs = list(execution.logs_json or [])
                logs[-1] = {
                    **logs[-1],
                    "status": "SUCCESS",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "log_text": result.log,
                    "output_files": [str(path) for path in prev_outputs],
                }
                execution.logs_json = logs
                execution.progress_int = int(index / total_steps * 100)
                await session.commit()
                metrics.record_execution_update(status="RUNNING", queue_name=queue_name, duration=None)
                _emit_execution_event(
                    execution_id,
                    "progress",
                    {
                        "execution_id": execution_id,
                        "status": "RUNNING",
                        "progress_int": execution.progress_int,
                        "step": index - 1,
                        "tool": tool_name,
                    },
                )

            if execution.status == "CANCELED":
                execution.error_code = "EXECUTION_CANCELED"
                execution.error_message = "Execution canceled by user"
                execution.active_tool = None
                await session.commit()
                _emit_execution_event(execution_id, "done", {"execution_id": execution_id, "status": "CANCELED"})
                return

            result_path = _finalize_execution_result(execution_id=execution_id, output_root=output_root, output_files=prev_outputs)
            execution.result_path = str(result_path)
            execution.result_type = result_path.suffix.lstrip(".") or "bin"
            execution.outputs_json = [_serialize_output_file(path) for path in prev_outputs]
            execution.status = "SUCCESS"
            execution.progress_int = 100
            execution.active_tool = None
            await session.commit()
            metrics.record_execution_update(status="SUCCESS", queue_name=queue_name, duration=None)
            _emit_execution_event(execution_id, "done", {"execution_id": execution_id, "status": "SUCCESS", "progress_int": 100})
        except asyncio.CancelledError:
            execution.status = "CANCELED"
            execution.error_code = "EXECUTION_CANCELED"
            execution.error_message = "Execution canceled by user"
            execution.active_tool = None
            await session.commit()
            metrics.record_execution_update(status="CANCELED", queue_name=queue_name, duration=None)
            _emit_execution_event(execution_id, "done", {"execution_id": execution_id, "status": "CANCELED"})
            raise
        except PDFAgentError as exc:
            logger.warning("Execution %s failed with PDFAgentError %s: %s", execution_id, exc.code, exc.message)
            await _fail_execution(session, execution, code=exc.code, message=exc.message)
            metrics.record_execution_update(status="FAILED", queue_name=queue_name, duration=None)
            _emit_execution_event(execution_id, "done", {"execution_id": execution_id, "status": "FAILED", "error_code": exc.code})
        except Exception as exc:
            logger.exception("Execution %s failed unexpectedly", execution_id)
            await _fail_execution(session, execution, code="EXECUTION_FAILED", message=str(exc))
            metrics.record_execution_update(status="FAILED", queue_name=queue_name, duration=None)
            _emit_execution_event(execution_id, "done", {"execution_id": execution_id, "status": "FAILED", "error_code": "EXECUTION_FAILED"})


async def _fail_execution(session, execution: ExecutionRecord, *, code: str, message: str) -> None:
    logs = list(execution.logs_json or [])
    if logs and logs[-1].get("status") == "RUNNING":
        logs[-1] = {
            **logs[-1],
            "status": "FAILED",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "log_text": message,
        }
    execution.logs_json = logs
    execution.status = "FAILED"
    execution.error_code = code
    execution.error_message = message
    execution.active_tool = None
    await session.commit()


async def _resolve_step_inputs(session, input_refs: list[dict[str, Any]], prev_outputs: list[Path]) -> list[Path]:
    resolved: list[Path] = []
    file_ids: list[uuid.UUID] = []
    raw_file_ids: list[str] = []
    for input_ref in input_refs:
        if input_ref.get("type") == "prev":
            resolved.extend(prev_outputs)
        elif input_ref.get("type") == "file":
            raw_id = str(input_ref.get("file_id"))
            raw_file_ids.append(raw_id)
            file_ids.append(uuid.UUID(raw_id))

    if file_ids:
        result = await session.execute(select(FileRecord).where(FileRecord.id.in_(file_ids)))
        records = {str(record.id): record for record in result.scalars().all()}
        for raw_id in raw_file_ids:
            record = records.get(raw_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"File {raw_id} not found")
            resolved.append(Path(record.storage_path))
    return resolved


def _store_step_outputs(*, output_files: list[Path], output_root: Path, is_final_step: bool) -> list[Path]:
    stored_outputs: list[Path] = []
    for index, output_file in enumerate(output_files, start=1):
        if not output_file.exists():
            continue
        target = output_file
        if is_final_step:
            target = output_root / _dedupe_output_name(output_root, output_file.name, index)
            if output_file.resolve() != target.resolve():
                shutil.copy2(output_file, target)
        stored_outputs.append(target)
    return stored_outputs


def _serialize_output_file(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def _dedupe_output_name(output_root: Path, filename: str, index: int) -> str:
    candidate = output_root / filename
    if not candidate.exists():
        return filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    return f"{stem}_{index}{suffix}"


def _finalize_execution_result(*, execution_id: str, output_root: Path, output_files: list[Path]) -> Path:
    if not output_files:
        raise HTTPException(status_code=500, detail="Execution produced no output")
    if len(output_files) == 1:
        return output_files[0]

    zip_path = output_root / f"{execution_id}_pipeline.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for output_file in output_files:
            zf.write(output_file, arcname=output_file.name)
    return zip_path


def _build_file_response(path: Path) -> ExecutionFileResponse:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return ExecutionFileResponse(path=str(path), filename=path.name, media_type=media_type)
