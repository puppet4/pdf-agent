"""WebSocket chat endpoint — bidirectional alternative to SSE with cancel support."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from pdf_agent.api.agent import (
    _extract_output_files,
    _paths_to_download_urls,
    _resolve_uploaded_files,
    _sanitize_tool_args,
)
from pdf_agent.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent-ws"])


async def _send_json(ws: WebSocket, event: str, data: dict):
    if ws.client_state == WebSocketState.CONNECTED:
        await ws.send_json({"event": event, **data})


@router.websocket("/ws")
async def ws_chat(ws: WebSocket):
    """WebSocket endpoint for bidirectional agent chat.

    Client sends:
      {"action": "chat", "message": "...", "thread_id": "...", "file_ids": [...]}
      {"action": "cancel"}

    Server sends:
      {"event": "thread", "thread_id": "..."}
      {"event": "token", "content": "..."}
      {"event": "tool_start", "tool": "...", "args": {...}}
      {"event": "tool_progress", "tool": "...", "elapsed_seconds": 1.2}
      {"event": "tool_end", "tool": "...", "output": "...", "files": [...]}
      {"event": "error", "message": "..."}
      {"event": "done"}
    """
    await ws.accept()
    graph = ws.app.state.graph
    if graph is None:
        await _send_json(ws, "error", {"message": "Agent not initialized"})
        await ws.close()
        return

    current_task: asyncio.Task | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json(ws, "error", {"message": "Invalid JSON"})
                continue

            action = msg.get("action")

            if action == "cancel":
                if current_task and not current_task.done():
                    current_task.cancel()
                    await _send_json(ws, "done", {})
                continue

            if action != "chat":
                await _send_json(ws, "error", {"message": f"Unknown action: {action}"})
                continue

            # Cancel any running task
            if current_task and not current_task.done():
                current_task.cancel()

            thread_id = msg.get("thread_id") or str(uuid.uuid4())
            message = msg.get("message", "")
            file_ids = msg.get("file_ids", [])

            current_task = asyncio.create_task(
                _stream_agent(ws, graph, thread_id, message, file_ids)
            )

    except WebSocketDisconnect:
        if current_task and not current_task.done():
            current_task.cancel()
    except Exception as exc:
        logger.exception("WebSocket error")
        try:
            await _send_json(ws, "error", {"message": str(exc)})
        except Exception:
            pass


async def _stream_agent(
    ws: WebSocket,
    graph,
    thread_id: str,
    message: str,
    file_ids: list[str],
):
    """Run the agent and push events through the WebSocket."""
    thread_workdir = settings.threads_dir / thread_id
    thread_workdir.mkdir(parents=True, exist_ok=True)

    uploaded_files = await _resolve_uploaded_files(file_ids)
    uploaded_paths = [f["path"] for f in uploaded_files]

    input_state: dict = {
        "messages": [{"role": "user", "content": message}],
        "thread_workdir": str(thread_workdir),
    }
    if uploaded_files:
        input_state["files"] = uploaded_files
        input_state["current_files"] = uploaded_paths

    config = {"configurable": {"thread_id": thread_id}}
    await _send_json(ws, "thread", {"thread_id": thread_id})

    tool_active = None
    tool_start_time = None

    try:
        aiter = graph.astream_events(input_state, config=config, version="v2").__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(aiter.__anext__(), timeout=5.0)
            except asyncio.TimeoutError:
                if tool_active and tool_start_time:
                    elapsed = time.time() - tool_start_time
                    await _send_json(ws, "tool_progress", {
                        "tool": tool_active,
                        "elapsed_seconds": round(elapsed, 1),
                    })
                continue
            except StopAsyncIteration:
                break

            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    await _send_json(ws, "token", {"content": chunk.content})

            elif kind == "on_tool_start":
                tool_active = event["name"]
                tool_start_time = time.time()
                raw_args = event["data"].get("input", {})
                await _send_json(ws, "tool_start", {
                    "tool": event["name"],
                    "args": _sanitize_tool_args(raw_args) if isinstance(raw_args, dict) else {},
                })

            elif kind == "on_tool_end":
                elapsed = time.time() - tool_start_time if tool_start_time else 0
                output = event["data"].get("output", "")
                file_paths = _extract_output_files(output)
                download_urls = _paths_to_download_urls(thread_id, file_paths)
                await _send_json(ws, "tool_end", {
                    "tool": event["name"],
                    "output": str(output)[:500],
                    "files": download_urls,
                    "elapsed_seconds": round(elapsed, 1),
                })
                tool_active = None
                tool_start_time = None

    except asyncio.CancelledError:
        await _send_json(ws, "done", {})
        return
    except Exception as exc:
        logger.exception("Agent stream error (ws)")
        await _send_json(ws, "error", {"message": str(exc)})

    await _send_json(ws, "done", {})
