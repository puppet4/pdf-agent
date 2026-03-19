"""Agent API — SSE streaming chat + file access + thread management."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from pdf_agent.agent.state import FileInfo
from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import FileRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Keys to strip from tool_start args (injected by our tool node, not user-facing)
_INTERNAL_KEYS = {"state", "tool_call_id"}

# Heartbeat interval during tool execution (seconds)
_HEARTBEAT_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str
    file_ids: list[str] = []


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
            result = await session.execute(
                select(FileRecord).where(FileRecord.id == uuid.UUID(fid))
            )
            record = result.scalar_one_or_none()
            if record:
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
    return {k: v for k, v in args.items() if k not in _INTERNAL_KEYS}


def _paths_to_download_urls(thread_id: str, file_paths: list[str]) -> list[str]:
    """Convert absolute file paths to API download URLs.

    Encodes step directory into the URL to avoid filename collisions:
    /api/agent/threads/{thread_id}/files/{step}/{filename}
    """
    urls = []
    for fp in file_paths:
        p = Path(fp)
        step = p.parent.name  # e.g. "step_0"
        urls.append(f"/api/agent/threads/{thread_id}/files/{step}/{p.name}")
    return urls


def _extract_output_files(output: str) -> list[str]:
    """Extract file paths from tool output string."""
    if not isinstance(output, str):
        return []
    for line in output.splitlines():
        if line.startswith("Output files:"):
            raw = line[len("Output files:"):].strip()
            try:
                return json.loads(raw.replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                pass
    return []


# ---------------------------------------------------------------------------
# SSE Chat endpoint (with heartbeat progress)
# ---------------------------------------------------------------------------

@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Stream a conversation turn with the LangGraph agent via SSE."""
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    thread_id = req.thread_id or str(uuid.uuid4())

    # Create thread workdir
    thread_workdir = settings.threads_dir / thread_id
    thread_workdir.mkdir(parents=True, exist_ok=True)

    # Resolve uploaded files
    uploaded_files = await _resolve_uploaded_files(req.file_ids)
    uploaded_paths = [f["path"] for f in uploaded_files]

    # Build input state
    input_state: dict = {
        "messages": [{"role": "user", "content": req.message}],
        "thread_workdir": str(thread_workdir),
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

        yield _sse_event("done", {})
        release_progress_queue(thread_id)

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
    thread_dir = settings.threads_dir / thread_id
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    files = []
    for step_dir in sorted(thread_dir.iterdir()):
        if step_dir.is_dir() and step_dir.name.startswith("step_"):
            for f in step_dir.iterdir():
                if f.is_file():
                    files.append({
                        "step": step_dir.name,
                        "filename": f.name,
                        "size_bytes": f.stat().st_size,
                        "download_url": f"/api/agent/threads/{thread_id}/files/{step_dir.name}/{f.name}",
                    })
    return {"thread_id": thread_id, "files": files}


@router.get("/threads/{thread_id}/files/{step}/{filename}")
async def download_thread_file(thread_id: str, step: str, filename: str):
    """Download a specific file from a thread's step directory."""
    thread_dir = settings.threads_dir / thread_id
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    candidate = thread_dir / step / filename
    if candidate.is_file():
        return FileResponse(candidate, filename=filename)

    raise HTTPException(status_code=404, detail=f"File '{step}/{filename}' not found in thread")


# ---------------------------------------------------------------------------
# Thread management endpoints
# ---------------------------------------------------------------------------

@router.get("/threads")
async def list_threads():
    """List all conversation threads."""
    threads_dir = settings.threads_dir
    if not threads_dir.exists():
        return {"threads": []}

    threads = []
    for entry in sorted(threads_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
        if entry.is_dir():
            stat = entry.stat()
            step_count = sum(1 for d in entry.iterdir() if d.is_dir() and d.name.startswith("step_"))
            threads.append({
                "thread_id": entry.name,
                "created_at": stat.st_ctime,
                "updated_at": stat.st_mtime,
                "step_count": step_count,
            })
    return {"threads": threads}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    """Get thread details including conversation history from checkpointer."""
    thread_dir = settings.threads_dir / thread_id
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
                            {"name": tc["name"], "args": tc["args"]}
                            for tc in msg.tool_calls
                        ]
                    if hasattr(msg, "name") and msg.name:
                        msg_data["name"] = msg.name
                    messages.append(msg_data)
        except Exception:
            logger.warning("Failed to load thread state for %s", thread_id)

    stat = thread_dir.stat()
    return {
        "thread_id": thread_id,
        "created_at": stat.st_ctime,
        "updated_at": stat.st_mtime,
        "messages": messages,
    }


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a thread and its workdir."""
    thread_dir = settings.threads_dir / thread_id
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    shutil.rmtree(thread_dir)
    return {"deleted": True, "thread_id": thread_id}


# ---------------------------------------------------------------------------
# Batch operations endpoint
# ---------------------------------------------------------------------------

class BatchRequest(BaseModel):
    """Run the same tool on multiple files in parallel, each in its own thread."""
    tool_name: str
    file_ids: list[str]
    tool_params: dict = {}


@router.post("/batch")
async def batch_run(req: BatchRequest, request: Request):
    """Run a tool on multiple files concurrently. Returns SSE stream."""
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    if not req.file_ids:
        raise HTTPException(status_code=422, detail="file_ids must not be empty")

    # Build one message per file asking the agent to apply the tool
    params_str = ", ".join(f"{k}={v}" for k, v in req.tool_params.items())
    param_clause = f" with {params_str}" if params_str else ""

    async def event_stream():
        tasks = []
        for fid in req.file_ids:
            thread_id = str(uuid.uuid4())
            msg = f"Apply {req.tool_name}{param_clause} to this file."
            tasks.append((fid, thread_id, msg))

        yield _sse_event("batch_start", {"count": len(tasks), "tool": req.tool_name})

        async def run_one(fid: str, thread_id: str, msg: str):
            uploaded = await _resolve_uploaded_files([fid])
            if not uploaded:
                return thread_id, fid, None, "File not found"
            thread_workdir = settings.threads_dir / thread_id
            thread_workdir.mkdir(parents=True, exist_ok=True)
            input_state: dict = {
                "messages": [{"role": "user", "content": msg}],
                "thread_workdir": str(thread_workdir),
                "files": uploaded,
                "current_files": [f["path"] for f in uploaded],
            }
            config = {"configurable": {"thread_id": thread_id}}
            output = ""
            files_out: list[str] = []
            try:
                async for event in graph.astream_events(input_state, config=config, version="v2"):
                    if event["event"] == "on_tool_end":
                        out = event["data"].get("output", "")
                        output = str(out)[:300]
                        files_out = _paths_to_download_urls(thread_id, _extract_output_files(out))
            except Exception as e:
                return thread_id, fid, None, str(e)
            return thread_id, fid, files_out, output

        results = await asyncio.gather(*[run_one(fid, tid, msg) for fid, tid, msg in tasks])

        for thread_id, fid, files_out, output in results:
            yield _sse_event("batch_result", {
                "file_id": fid,
                "thread_id": thread_id,
                "files": files_out or [],
                "output": output,
            })

        yield _sse_event("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
