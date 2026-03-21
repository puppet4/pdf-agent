"""Agent API — chat endpoints plus plan-preview execution creation."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select

from pdf_agent.agent.state import FileInfo
from pdf_agent.agent.tools_adapter import parse_tool_result_payload
from pdf_agent.api.executions import create_execution_record
from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import FileRecord
from pdf_agent.tools.registry import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Keys to strip from tool_start args (injected by our tool node, not user-facing)
_INTERNAL_KEYS = {"state", "tool_call_id", "progress_reporter"}
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_ARG_RE = re.compile(r"(password|secret|token|api[_-]?key|authorization)", re.IGNORECASE)

# Heartbeat interval during tool execution (seconds)
_HEARTBEAT_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str
    file_ids: list[str] = []


class AgentPlanRequest(BaseModel):
    message: str
    file_ids: list[str] = []


class AgentPlanConfirmRequest(BaseModel):
    mode: str = "AGENT"
    instruction: str
    plan: dict


class PlannerStep(BaseModel):
    tool: str
    params: dict = Field(default_factory=dict)


class PlannerResponse(BaseModel):
    steps: list[PlannerStep] = Field(default_factory=list)
    output: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_uploaded_files(file_ids: list[str]) -> list[FileInfo]:
    """Look up FileRecords by id and convert to FileInfo."""
    if not file_ids:
        return []
    files: list[FileInfo] = []
    async with async_session_factory() as session:
        for fid in file_ids:
            try:
                parsed_id = uuid.UUID(fid)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid file_id: {fid}") from exc
            result = await session.execute(
                select(FileRecord).where(FileRecord.id == parsed_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail=f"File {fid} not found")
            if not Path(record.storage_path).exists():
                raise HTTPException(status_code=404, detail=f"File {fid} not on disk")
            files.append(FileInfo(
                file_id=str(record.id),
                path=record.storage_path,
                orig_name=record.orig_name,
                mime_type=record.mime_type,
                page_count=record.page_count,
                source="upload",
            ))
    return files


def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sanitize_tool_args(args: dict) -> dict:
    """Remove internal keys that should not be exposed to the client."""
    sanitized: dict = {}
    for key, value in args.items():
        if key in _INTERNAL_KEYS:
            continue
        sanitized[key] = _sanitize_arg_value(key, value)
    return sanitized


def _sanitize_arg_value(key: str, value):
    if _SENSITIVE_ARG_RE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {child_key: _sanitize_arg_value(child_key, child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_arg_value(key, item) for item in value]
    return value


def _paths_to_download_urls(thread_id: str, file_paths: list[str]) -> list[str]:
    """Convert absolute file paths to API download URLs.

    Preserves nested output paths under the thread directory:
    /api/agent/threads/{thread_id}/files/{step}/nested/{filename}
    """
    urls = []
    thread_dir = _resolve_thread_dir(thread_id)
    for fp in file_paths:
        p = Path(fp)
        try:
            rel = p.resolve().relative_to(thread_dir)
        except ValueError:
            parts = p.parts
            if thread_id in parts:
                rel = Path(*parts[parts.index(thread_id) + 1 :])
            else:
                step_index = next((idx for idx, part in enumerate(parts) if part.startswith("step_")), None)
                if step_index is None:
                    continue
                rel = Path(*parts[step_index:])
        urls.append(f"/api/agent/threads/{thread_id}/files/{rel.as_posix()}")
    return urls


def _validate_thread_id(thread_id: str, *, status_code: int) -> str:
    """Allow only simple thread ids that stay within the threads directory."""
    if not thread_id or not _THREAD_ID_RE.fullmatch(thread_id):
        raise HTTPException(status_code=status_code, detail="Invalid thread_id")
    return thread_id


def _resolve_thread_dir(thread_id: str) -> Path:
    """Resolve a thread directory and reject path traversal."""
    safe_thread_id = _validate_thread_id(thread_id, status_code=400)
    base_dir = settings.threads_dir.resolve()
    candidate = (base_dir / safe_thread_id).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread path") from exc
    return candidate


def _resolve_thread_file_path(thread_id: str, file_path: str, filename: str | None = None) -> Path:
    """Resolve a file inside a thread directory and reject traversal."""
    if filename is not None:
        file_path = f"{file_path}/{filename}"
    if not file_path or file_path.startswith("/") or ".." in Path(file_path).parts:
        raise HTTPException(status_code=400, detail="Invalid thread file path")
    thread_dir = _resolve_thread_dir(thread_id)
    if not file_path.split("/", 1)[0].startswith("step_"):
        raise HTTPException(status_code=400, detail="Invalid thread file path")
    candidate = (thread_dir / file_path).resolve()
    try:
        candidate.relative_to(thread_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread file path") from exc
    return candidate


def _extract_output_files(output: str) -> list[str]:
    """Extract file paths from tool output string."""
    if not isinstance(output, str):
        return []
    return parse_tool_result_payload(output).output_files


def _planner_tool_catalog() -> str:
    lines: list[str] = []
    for tool in registry.list_all():
        manifest = tool.manifest()
        params = ", ".join(
            f"{param.name}:{param.type}{'*' if param.required else ''}"
            for param in manifest.params
        ) or "none"
        lines.append(
            f"- {manifest.name}: {manifest.description or manifest.label}; "
            f"inputs={manifest.inputs.min}-{manifest.inputs.max}; params={params}"
        )
    return "\n".join(lines)


def _planner_file_context(files: list[FileInfo]) -> str:
    lines = []
    for file in files:
        page_count = file.get("page_count")
        page_text = f", pages={page_count}" if page_count is not None else ""
        lines.append(
            f"- file_id={file['file_id']}, name={file['orig_name']}, mime={file['mime_type']}{page_text}"
        )
    return "\n".join(lines)


def _build_planner_llm() -> ChatOpenAI:
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="Agent plan preview requires PDF_AGENT_OPENAI_API_KEY")
    kwargs = {
        "model": settings.openai_model,
        "temperature": settings.agent_temperature,
        "api_key": settings.openai_api_key,
    }
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


async def _generate_plan_with_langchain(message: str, files: list[FileInfo]) -> dict:
    planner = _build_planner_llm().with_structured_output(PlannerResponse)
    response = await planner.ainvoke(
        [
            SystemMessage(
                content=(
                    "You are a PDF tool planner. Build an executable plan using only the listed tools. "
                    "Return the minimum step sequence needed for the user request. "
                    "Do not invent tool names or parameter names. "
                    "The first step will receive the uploaded files automatically; later steps will receive previous outputs automatically."
                )
            ),
            HumanMessage(
                content=(
                    f"User request:\n{message}\n\n"
                    f"Uploaded files:\n{_planner_file_context(files)}\n\n"
                    f"Available tools:\n{_planner_tool_catalog()}\n\n"
                    "Return only the structured plan."
                )
            ),
        ]
    )
    return _normalize_planner_response(response, [file["file_id"] for file in files])


def _normalize_planner_response(planner_response: PlannerResponse, file_ids: list[str]) -> dict:
    normalized_steps: list[dict] = []
    for index, step in enumerate(planner_response.steps):
        tool = registry.get(step.tool)
        if tool is None:
            raise HTTPException(status_code=422, detail=f"Planner returned unknown tool: {step.tool}")
        try:
            normalized_params = tool.validate(step.params or {})
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Planner returned invalid params for {step.tool}: {exc}") from exc
        normalized_steps.append(
            {
                "tool": step.tool,
                "inputs": [{"type": "file", "file_id": file_id} for file_id in file_ids] if index == 0 else [{"type": "prev"}],
                "params": normalized_params,
            }
        )
    if not normalized_steps:
        raise HTTPException(status_code=422, detail="Planner returned no executable steps")
    output = planner_response.output if isinstance(planner_response.output, dict) else {}
    return {"version": "1.0", "steps": normalized_steps, "output": output}


@router.post("/plans/preview")
async def preview_agent_plan(req: AgentPlanRequest):
    if not req.file_ids:
        raise HTTPException(status_code=422, detail="file_ids must not be empty")
    files = await _resolve_uploaded_files(req.file_ids)
    plan = await _generate_plan_with_langchain(req.message, files)
    return {
        "instruction": req.message,
        "plan": plan,
        "requires_confirmation": True,
    }


@router.post("/plans/confirm")
async def confirm_agent_plan(req: AgentPlanConfirmRequest):
    return await create_execution_record(
        mode=req.mode,
        instruction=req.instruction,
        steps=req.plan.get("steps", []),
        file_ids=[],
        output=req.plan.get("output", {}),
    )


# ---------------------------------------------------------------------------
# SSE Chat endpoint (with heartbeat progress)
# ---------------------------------------------------------------------------

@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Stream a conversation turn with the LangGraph agent via SSE."""
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    thread_id = _validate_thread_id(req.thread_id, status_code=422) if req.thread_id else str(uuid.uuid4())

    # Resolve uploaded files
    uploaded_files = await _resolve_uploaded_files(req.file_ids)
    uploaded_paths = [f["path"] for f in uploaded_files]

    # Create thread workdir only after request validation succeeds
    thread_workdir = _resolve_thread_dir(thread_id)
    thread_workdir.mkdir(parents=True, exist_ok=True)

    # Build input state
    input_state: dict = {
        "messages": [{"role": "user", "content": req.message}],
        "thread_workdir": str(thread_workdir),
        "configurable": {"thread_id": thread_id},
    }
    if uploaded_files:
        input_state["files"] = uploaded_files
        input_state["current_files"] = uploaded_paths

    config = {"configurable": {"thread_id": thread_id}}

    async def event_stream():
        yield _sse_event("thread", {"thread_id": thread_id})

        tool_active = None
        tool_start_time = None

        # Import progress queue helpers
        from pdf_agent.agent.tools_adapter import get_progress_queue, release_progress_queue
        prog_queue = get_progress_queue(thread_id)

        try:
            aiter = graph.astream_events(input_state, config=config, version="v2").__aiter__()
            while True:
                try:
                    event = await asyncio.wait_for(aiter.__anext__(), timeout=_HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    # Drain progress queue
                    while True:
                        try:
                            prog = prog_queue.get_nowait()
                            yield _sse_event("tool_progress", {
                                "tool": tool_active or "",
                                "percent": prog.get("percent", 0),
                                "message": prog.get("message", ""),
                                "elapsed_seconds": round(time.time() - tool_start_time, 1) if tool_start_time else 0,
                            })
                        except queue.Empty:
                            break
                    # Fallback heartbeat if no progress events
                    if tool_active and tool_start_time:
                        elapsed = time.time() - tool_start_time
                        yield _sse_event("tool_progress", {
                            "tool": tool_active,
                            "elapsed_seconds": round(elapsed, 1),
                        })
                    continue
                except StopAsyncIteration:
                    break

                kind = event["event"]

                # LLM token streaming
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

                # Tool start
                elif kind == "on_tool_start":
                    tool_active = event["name"]
                    tool_start_time = time.time()
                    raw_args = event["data"].get("input", {})
                    yield _sse_event("tool_start", {
                        "tool": event["name"],
                        "args": _sanitize_tool_args(raw_args) if isinstance(raw_args, dict) else {},
                    })

                # Tool end — convert paths to download URLs, include elapsed time
                elif kind == "on_tool_end":
                    elapsed = time.time() - tool_start_time if tool_start_time else 0
                    output = event["data"].get("output", "")
                    file_paths = _extract_output_files(output)
                    download_urls = _paths_to_download_urls(thread_id, file_paths)
                    yield _sse_event("tool_end", {
                        "tool": event["name"],
                        "output": str(output)[:500],
                        "files": download_urls,
                        "elapsed_seconds": round(elapsed, 1),
                    })
                    tool_active = None
                    tool_start_time = None

        except Exception as e:
            logger.exception("Agent stream error")
            yield _sse_event("error", {"message": str(e)})
        finally:
            release_progress_queue(thread_id)

        yield _sse_event("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Thread file endpoints (step-aware to avoid collisions)
# ---------------------------------------------------------------------------

@router.get("/threads/{thread_id}/files")
async def list_thread_files(thread_id: str):
    """List all output files in a thread's workdir."""
    thread_dir = _resolve_thread_dir(thread_id)
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    files = []
    for step_dir in sorted(thread_dir.iterdir()):
        try:
            if not step_dir.is_dir() or not step_dir.name.startswith("step_"):
                continue
        except OSError:
            logger.warning("Failed to inspect thread step dir for %s", thread_id, exc_info=True)
            continue
        for f in step_dir.rglob("*"):
            try:
                if not f.is_file():
                    continue
                rel = f.relative_to(thread_dir).as_posix()
                item = {
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                    "download_url": f"/api/agent/threads/{thread_id}/files/{rel}",
                }
                if len(Path(rel).parts) > 2:
                    item["path"] = rel
                files.append(item)
            except OSError:
                logger.warning("Failed to inspect thread output %s", f, exc_info=True)
                continue
    return {"thread_id": thread_id, "files": files}


@router.get("/threads/{thread_id}/files/{file_path:path}")
async def download_thread_file(thread_id: str, file_path: str):
    """Download a specific file from a thread's step directory."""
    thread_dir = _resolve_thread_dir(thread_id)
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    candidate = _resolve_thread_file_path(thread_id, file_path)
    if candidate.is_file():
        return FileResponse(candidate, filename=candidate.name)

    raise HTTPException(status_code=404, detail=f"File '{file_path}' not found in thread")


# ---------------------------------------------------------------------------
# Thread management endpoints
# ---------------------------------------------------------------------------

@router.get("/threads")
async def list_threads(page: int = 1, limit: int = 100):
    """List all conversation threads."""
    threads_dir = settings.threads_dir
    if not threads_dir.exists():
        return {"threads": []}

    threads = []
    for entry in threads_dir.iterdir():
        try:
            if not entry.is_dir() or entry.name.startswith("direct_"):
                continue
            stat = entry.stat()
            step_count = sum(1 for d in entry.iterdir() if d.is_dir() and d.name.startswith("step_"))
            threads.append({
                "thread_id": entry.name,
                "created_at": stat.st_ctime,
                "updated_at": stat.st_mtime,
                "step_count": step_count,
            })
        except OSError:
            logger.warning("Failed to inspect thread %s", entry, exc_info=True)
            continue
    threads.sort(key=lambda item: item["updated_at"], reverse=True)
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    total = len(threads)
    start = (page - 1) * limit
    end = start + limit
    return {"threads": threads[start:end], "total": total, "page": page, "limit": limit}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    """Get thread details including conversation history from checkpointer."""
    thread_dir = _resolve_thread_dir(thread_id)
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    # Get conversation history from checkpointer
    graph = request.app.state.graph
    messages = []
    if graph is not None:
        try:
            config = {"configurable": {"thread_id": thread_id}}
            state = await graph.aget_state(config)
            if state and state.values:
                for msg in state.values.get("messages", []):
                    msg_data = {"type": msg.type, "content": ""}
                    if hasattr(msg, "content"):
                        content = msg.content
                        msg_data["content"] = content if isinstance(content, str) else str(content)
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        msg_data["tool_calls"] = [
                            {"name": tc["name"], "args": _sanitize_tool_args(tc["args"])}
                            for tc in msg.tool_calls
                        ]
                    if hasattr(msg, "name") and msg.name:
                        msg_data["name"] = msg.name
                    messages.append(msg_data)
        except Exception:
            logger.warning("Failed to load thread state for %s", thread_id, exc_info=True)
            raise HTTPException(status_code=503, detail="Thread state unavailable")

    stat = thread_dir.stat()
    return {
        "thread_id": thread_id,
        "created_at": stat.st_ctime,
        "updated_at": stat.st_mtime,
        "messages": messages,
    }


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request):
    """Delete a thread and its workdir."""
    thread_dir = _resolve_thread_dir(thread_id)
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.warning("Failed to delete checkpoint state for %s", thread_id, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to delete thread state")
    shutil.rmtree(thread_dir)
    return {"deleted": True, "thread_id": thread_id}
