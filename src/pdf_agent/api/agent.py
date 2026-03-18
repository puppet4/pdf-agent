"""Agent API — SSE streaming chat + file access."""
from __future__ import annotations

import json
import logging
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


# ---------------------------------------------------------------------------
# SSE Chat endpoint
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

        try:
            async for event in graph.astream_events(input_state, config=config, version="v2"):
                kind = event["event"]

                # LLM token streaming
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

                # Tool start
                elif kind == "on_tool_start":
                    yield _sse_event("tool_start", {
                        "tool": event["name"],
                        "args": event["data"].get("input", {}),
                    })

                # Tool end
                elif kind == "on_tool_end":
                    output = event["data"].get("output", "")
                    # Extract file paths from output
                    files = []
                    if isinstance(output, str):
                        for line in output.splitlines():
                            if line.startswith("Output files:"):
                                raw = line[len("Output files:"):].strip()
                                try:
                                    files = json.loads(raw.replace("'", '"'))
                                except (json.JSONDecodeError, ValueError):
                                    pass
                    yield _sse_event("tool_end", {
                        "tool": event["name"],
                        "output": str(output)[:500],
                        "files": files,
                    })

        except Exception as e:
            logger.exception("Agent stream error")
            yield _sse_event("error", {"message": str(e)})

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
# Thread file endpoints
# ---------------------------------------------------------------------------

@router.get("/threads/{thread_id}/files")
async def list_thread_files(thread_id: str, request: Request):
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
                        "path": str(f),
                    })
    return {"thread_id": thread_id, "files": files}


@router.get("/threads/{thread_id}/files/{filename}")
async def download_thread_file(thread_id: str, filename: str):
    """Download a specific file from a thread by searching all step dirs."""
    thread_dir = settings.threads_dir / thread_id
    if not thread_dir.exists():
        raise HTTPException(status_code=404, detail="Thread not found")

    # Search step dirs for the file
    for step_dir in sorted(thread_dir.iterdir()):
        if step_dir.is_dir():
            candidate = step_dir / filename
            if candidate.is_file():
                return FileResponse(candidate, filename=filename)

    raise HTTPException(status_code=404, detail=f"File '{filename}' not found in thread")
