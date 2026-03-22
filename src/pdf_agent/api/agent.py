"""Conversation API — chat streaming and artifact access."""
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
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import select

from pdf_agent.agent.intent_hints import build_intent_hints
from pdf_agent.agent.state import FileInfo
from pdf_agent.agent.tools_adapter import parse_tool_result_payload
from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.db.models import FileRecord

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

# Keys to strip from tool_start args (injected by our tool node, not user-facing)
_INTERNAL_KEYS = {"state", "tool_call_id", "progress_reporter"}
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_ARG_RE = re.compile(r"(password|secret|token|api[_-]?key|authorization)", re.IGNORECASE)

# Heartbeat interval during a tool run (seconds)
_HEARTBEAT_INTERVAL = 5.0
_DEFAULT_CONVERSATION_TITLE = "新会话"
_MAX_CONVERSATION_TITLE_LENGTH = 48


def _content_disposition_headers(filename: str, *, inline: bool) -> dict[str, str]:
    disposition = "inline" if inline else "attachment"
    safe_name = filename.replace("\\", "_").replace("\r", "").replace("\n", "").replace('"', "")
    ascii_fallback = safe_name.encode("ascii", "ignore").decode("ascii").strip(" .")
    if not ascii_fallback:
        suffix = Path(safe_name).suffix.encode("ascii", "ignore").decode("ascii")
        ascii_fallback = f"download{suffix}" if suffix else "download"
    encoded_name = quote(safe_name, safe="")
    return {
        "Content-Disposition": (
            f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_name}'
        )
    }


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class MessageCreateRequest(BaseModel):
    message: str
    file_ids: list[str] = []
    artifact_paths: list[str] = []


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


def _artifact_path_to_file_info(conversation_id: str, artifact_path: str) -> FileInfo:
    resolved = _resolve_conversation_artifact_path(conversation_id, artifact_path)
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_path}")
    guessed_mime = "application/pdf" if resolved.suffix.lower() == ".pdf" else "application/octet-stream"
    page_count = None
    if guessed_mime == "application/pdf":
        try:
            import pikepdf
            with pikepdf.open(resolved) as pdf:
                page_count = len(pdf.pages)
        except Exception:
            page_count = None
    return FileInfo(
        file_id=f"artifact:{artifact_path}",
        path=str(resolved),
        orig_name=resolved.name,
        mime_type=guessed_mime,
        page_count=page_count,
        source="artifact",
    )


def _resolve_selected_artifacts(conversation_id: str, artifact_paths: list[str]) -> list[FileInfo]:
    if not artifact_paths:
        return []
    seen: set[str] = set()
    files: list[FileInfo] = []
    for artifact_path in artifact_paths:
        if artifact_path in seen:
            continue
        seen.add(artifact_path)
        files.append(_artifact_path_to_file_info(conversation_id, artifact_path))
    return files


def _serialize_selected_input(file_info: FileInfo, conversation_id: str) -> dict[str, str]:
    path = str(file_info["path"])
    item = {
        "name": file_info["orig_name"],
        "source": file_info["source"],
        "type": file_info["mime_type"],
    }
    if file_info["source"] == "artifact":
        item["path"] = _paths_to_download_urls(conversation_id, [path])[0]
    else:
        item["file_id"] = file_info["file_id"]
    return item


def _build_message_input_state(
    *,
    message: str,
    human_message_kwargs: dict[str, object],
    conversation_workdir: Path,
    conversation_id: str,
    selected_inputs: list[FileInfo],
) -> dict:
    selected_paths = [f["path"] for f in selected_inputs]
    input_state: dict = {
        "messages": [
            HumanMessage(
                content=message,
                additional_kwargs=human_message_kwargs,
            )
        ],
        "conversation_workdir": str(conversation_workdir),
        "configurable": {"thread_id": conversation_id},
    }
    if selected_inputs:
        input_state["files"] = selected_inputs
        input_state["current_files"] = selected_paths
    return input_state


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


def _paths_to_download_urls(conversation_id: str, file_paths: list[str]) -> list[str]:
    """Convert absolute file paths to conversation artifact URLs."""
    urls = []
    conversation_dir = _resolve_conversation_dir(conversation_id)
    for fp in file_paths:
        p = Path(fp)
        try:
            rel = p.resolve().relative_to(conversation_dir)
        except ValueError:
            parts = p.parts
            if conversation_id in parts:
                rel = Path(*parts[parts.index(conversation_id) + 1 :])
            else:
                step_index = next((idx for idx, part in enumerate(parts) if part.startswith("step_")), None)
                if step_index is None:
                    continue
                rel = Path(*parts[step_index:])
        urls.append(f"/api/conversations/{conversation_id}/artifacts/{rel.as_posix()}")
    return urls


def _validate_conversation_id(conversation_id: str, *, status_code: int) -> str:
    """Allow only simple conversation ids that stay within the conversation storage directory."""
    if not conversation_id or not _CONVERSATION_ID_RE.fullmatch(conversation_id):
        raise HTTPException(status_code=status_code, detail="Invalid conversation_id")
    return conversation_id


def _resolve_conversation_dir(conversation_id: str) -> Path:
    """Resolve a conversation directory and reject path traversal."""
    safe_conversation_id = _validate_conversation_id(conversation_id, status_code=400)
    base_dir = settings.conversations_dir.resolve()
    candidate = (base_dir / safe_conversation_id).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation path") from exc
    return candidate


def _resolve_conversation_artifact_path(
    conversation_id: str,
    artifact_path: str,
    filename: str | None = None,
) -> Path:
    """Resolve a file inside a conversation directory and reject traversal."""
    if filename is not None:
        artifact_path = f"{artifact_path}/{filename}"
    if not artifact_path or artifact_path.startswith("/") or ".." in Path(artifact_path).parts:
        raise HTTPException(status_code=400, detail="Invalid conversation artifact path")
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not artifact_path.split("/", 1)[0].startswith("step_"):
        raise HTTPException(status_code=400, detail="Invalid conversation artifact path")
    candidate = (conversation_dir / artifact_path).resolve()
    try:
        candidate.relative_to(conversation_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid conversation artifact path") from exc
    return candidate


def _extract_output_files(output: str) -> list[str]:
    """Extract file paths from tool output string."""
    if not isinstance(output, str):
        return []
    return parse_tool_result_payload(output).output_files


def _conversation_title_path(conversation_dir: Path) -> Path:
    return conversation_dir / ".title.txt"


def _sanitize_conversation_title(raw: str) -> str:
    normalized = " ".join((raw or "").strip().split())
    if normalized == "New Conversation":
        normalized = _DEFAULT_CONVERSATION_TITLE
    if not normalized:
        return _DEFAULT_CONVERSATION_TITLE
    if len(normalized) > _MAX_CONVERSATION_TITLE_LENGTH:
        return normalized[: _MAX_CONVERSATION_TITLE_LENGTH - 1].rstrip() + "…"
    return normalized


def _read_conversation_title(conversation_dir: Path) -> str:
    title_path = _conversation_title_path(conversation_dir)
    if not title_path.exists():
        return _DEFAULT_CONVERSATION_TITLE
    try:
        return _sanitize_conversation_title(title_path.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("Failed to read conversation title from %s", title_path, exc_info=True)
        return _DEFAULT_CONVERSATION_TITLE


def _write_conversation_title(conversation_dir: Path, title: str) -> None:
    title_path = _conversation_title_path(conversation_dir)
    safe_title = _sanitize_conversation_title(title)
    try:
        title_path.write_text(safe_title, encoding="utf-8")
    except OSError:
        logger.warning("Failed to persist conversation title to %s", title_path, exc_info=True)


def _count_artifacts(conversation_dir: Path) -> int:
    total = 0
    for step_dir in conversation_dir.iterdir():
        try:
            if not step_dir.is_dir() or not step_dir.name.startswith("step_"):
                continue
        except OSError:
            continue
        total += sum(1 for candidate in step_dir.rglob("*") if candidate.is_file())
    return total


async def _load_conversation_messages(conversation_id: str, request: Request) -> list[dict]:
    """Load persisted conversation messages from LangGraph state."""
    graph = request.app.state.graph
    messages: list[dict] = []
    if graph is None:
        return messages

    try:
        config = {"configurable": {"thread_id": conversation_id}}
        state = await graph.aget_state(config)
        if state and state.values:
            pending_output_files: list[str] = []
            for msg in state.values.get("messages", []):
                if msg.type == "tool":
                    parsed = parse_tool_result_payload(
                        msg.content if isinstance(getattr(msg, "content", ""), str) else ""
                    )
                    pending_output_files.extend(parsed.output_files)
                    artifact = getattr(msg, "artifact", None)
                    if isinstance(artifact, dict):
                        output_files = artifact.get("output_files", [])
                        if isinstance(output_files, list):
                            pending_output_files.extend(
                                path for path in output_files if isinstance(path, str) and path
                            )
                    continue

                msg_data = {"type": msg.type, "content": ""}
                if hasattr(msg, "content"):
                    content = msg.content
                    msg_data["content"] = content if isinstance(content, str) else str(content)
                if msg.type == "human":
                    additional_kwargs = getattr(msg, "additional_kwargs", None)
                    if isinstance(additional_kwargs, dict):
                        selected_inputs = additional_kwargs.get("selected_inputs")
                        if isinstance(selected_inputs, list):
                            msg_data["attachments"] = [
                                item for item in selected_inputs
                                if isinstance(item, dict) and isinstance(item.get("name"), str)
                            ]
                if msg.type == "ai" and pending_output_files:
                    deduped_files = list(dict.fromkeys(pending_output_files))
                    msg_data["files"] = _paths_to_download_urls(conversation_id, deduped_files)
                    pending_output_files = []
                messages.append(msg_data)
    except Exception:
        logger.warning("Failed to load conversation state for %s", conversation_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Conversation state unavailable")
    return messages


def _serialize_conversation(conversation_dir: Path) -> dict:
    stat = conversation_dir.stat()
    return {
        "id": conversation_dir.name,
        "conversation_id": conversation_dir.name,
        "title": _read_conversation_title(conversation_dir),
        "created_at": stat.st_ctime,
        "updated_at": stat.st_mtime,
        "artifact_count": _count_artifacts(conversation_dir),
        "step_count": sum(1 for d in conversation_dir.iterdir() if d.is_dir() and d.name.startswith("step_")),
    }


def _list_artifacts(conversation_dir: Path, conversation_id: str) -> list[dict]:
    artifacts: list[dict] = []
    for step_dir in sorted(conversation_dir.iterdir()):
        try:
            if not step_dir.is_dir() or not step_dir.name.startswith("step_"):
                continue
        except OSError:
            logger.warning("Failed to inspect conversation step dir for %s", conversation_id, exc_info=True)
            continue
        for artifact in step_dir.rglob("*"):
            try:
                if not artifact.is_file():
                    continue
                rel = artifact.relative_to(conversation_dir).as_posix()
                artifacts.append(
                    {
                        "filename": artifact.name,
                        "path": rel,
                        "size_bytes": artifact.stat().st_size,
                        "download_url": f"/api/conversations/{conversation_id}/artifacts/{rel}",
                    }
                )
            except OSError:
                logger.warning("Failed to inspect conversation artifact %s", artifact, exc_info=True)
    return artifacts


# ---------------------------------------------------------------------------
# Conversation endpoints
# ---------------------------------------------------------------------------


@router.get("/api/conversations")
async def list_conversations(page: int = 1, limit: int = 100):
    """List all conversations."""
    conversations_dir = settings.conversations_dir
    if not conversations_dir.exists():
        return {"conversations": [], "total": 0, "page": 1, "limit": limit}

    conversations = []
    for entry in conversations_dir.iterdir():
        try:
            if not entry.is_dir() or entry.name.startswith("direct_"):
                continue
            conversations.append(_serialize_conversation(entry))
        except OSError:
            logger.warning("Failed to inspect conversation %s", entry, exc_info=True)
            continue

    conversations.sort(key=lambda item: item["updated_at"], reverse=True)
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    total = len(conversations)
    start = (page - 1) * limit
    end = start + limit
    return {"conversations": conversations[start:end], "total": total, "page": page, "limit": limit}


@router.post("/api/conversations")
async def create_conversation():
    """Create an empty conversation."""
    conversation_id = str(uuid.uuid4())
    conversation_dir = _resolve_conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True, exist_ok=False)
    _write_conversation_title(conversation_dir, _DEFAULT_CONVERSATION_TITLE)
    payload = _serialize_conversation(conversation_dir)
    payload["messages"] = []
    return payload


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    """Get conversation details including persisted message history."""
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    payload = _serialize_conversation(conversation_dir)
    payload["messages"] = await _load_conversation_messages(conversation_id, request)
    return payload


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    """Delete a conversation and its persisted state."""
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(conversation_id)
        except Exception:
            logger.warning("Failed to delete checkpoint state for %s", conversation_id, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to delete conversation state")

    shutil.rmtree(conversation_dir)
    return {"deleted": True, "id": conversation_id}


@router.get("/api/conversations/{conversation_id}/artifacts")
async def list_conversation_artifacts(conversation_id: str):
    """List generated artifacts for a conversation."""
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation_id": conversation_id, "artifacts": _list_artifacts(conversation_dir, conversation_id)}


@router.get("/api/conversations/{conversation_id}/artifacts/{artifact_path:path}")
async def download_conversation_artifact(
    conversation_id: str,
    artifact_path: str,
    inline: bool = Query(False, description="Return with inline Content-Disposition for preview"),
):
    """Download a generated artifact for a conversation."""
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    candidate = _resolve_conversation_artifact_path(conversation_id, artifact_path)
    if candidate.is_file():
        return FileResponse(
            candidate,
            filename=candidate.name,
            headers=_content_disposition_headers(candidate.name, inline=inline),
        )

    raise HTTPException(status_code=404, detail=f"Artifact '{artifact_path}' not found")


@router.post("/api/conversations/{conversation_id}/messages")
async def create_message(conversation_id: str, req: MessageCreateRequest, request: Request):
    """Stream a conversation turn with the LangGraph agent via SSE."""
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    conversation_id = _validate_conversation_id(conversation_id, status_code=422)

    # Resolve uploaded files
    uploaded_files = await _resolve_uploaded_files(req.file_ids)
    selected_artifacts = _resolve_selected_artifacts(conversation_id, req.artifact_paths)
    selected_inputs = uploaded_files + selected_artifacts
    # Create conversation workdir only after request validation succeeds
    conversation_workdir = _resolve_conversation_dir(conversation_id)
    conversation_workdir.mkdir(parents=True, exist_ok=True)
    current_title = _read_conversation_title(conversation_workdir)
    if current_title == _DEFAULT_CONVERSATION_TITLE:
        _write_conversation_title(conversation_workdir, req.message)

    # Build input state
    selected_input_summaries = [
        _serialize_selected_input(file_info, conversation_id)
        for file_info in selected_inputs
    ]
    normalized_intent_hints = build_intent_hints(req.message, selected_inputs)
    human_message_kwargs: dict[str, object] = {}
    if selected_input_summaries:
        human_message_kwargs["selected_inputs"] = selected_input_summaries
    if normalized_intent_hints:
        human_message_kwargs["normalized_intent_hints"] = normalized_intent_hints

    input_state = _build_message_input_state(
        message=req.message,
        human_message_kwargs=human_message_kwargs,
        conversation_workdir=conversation_workdir,
        conversation_id=conversation_id,
        selected_inputs=selected_inputs,
    )

    config = {"configurable": {"thread_id": conversation_id}}

    async def event_stream():
        yield _sse_event("conversation", {"conversation_id": conversation_id})

        # Import progress queue helpers
        from pdf_agent.agent.tools_adapter import get_progress_queue, release_progress_queue
        prog_queue = get_progress_queue(conversation_id)

        try:
            async for event in graph.astream_events(input_state, config=config, version="v2"):
                kind = event["event"]

                # LLM token streaming
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _sse_event("token", {"content": chunk.content})

                # Tool end — only expose output files, not internal tool details
                elif kind == "on_tool_end":
                    output = event["data"].get("output", "")
                    parsed = parse_tool_result_payload(output) if isinstance(output, str) else None
                    file_paths = parsed.output_files if parsed else _extract_output_files(output)
                    if file_paths:
                        yield _sse_event("artifact", {
                            "files": _paths_to_download_urls(conversation_id, file_paths),
                        })

        except Exception as e:
            logger.exception("Agent stream error")
            yield _sse_event("error", {"message": str(e)})
        finally:
            release_progress_queue(conversation_id)

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
