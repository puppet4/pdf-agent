"""Conversation API — chat streaming and artifact access."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import queue
import re
import shutil
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, BadRequestError, RateLimitError
from pydantic import BaseModel

from pdf_agent.agent.intent_hints import build_intent_hints
from pdf_agent.agent.state import FileInfo
from pdf_agent.agent.tools_adapter import parse_tool_result_payload
from pdf_agent.config import settings
from pdf_agent.db import async_session_factory
from pdf_agent.api.http import content_disposition_headers
from pdf_agent.api.metrics import metrics
from pdf_agent.external_commands import cancel_conversation_processes
from pdf_agent.services import FileService
from pdf_agent.services.conversation_history import append_history_message, load_history_messages
from pdf_agent.services.idempotency import build_request_hash, idempotency_service, normalize_idempotency_key

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
_CONVERSATION_STATS_CACHE_FILE = ".conversation_stats.json"

_content_disposition_headers = content_disposition_headers


@dataclass(frozen=True)
class ConversationMessagesLoadResult:
    messages: list[dict]
    source: str
    status: str
    warning: str | None = None


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
        svc = FileService(session)
        for fid in file_ids:
            try:
                parsed_id = uuid.UUID(fid)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid file_id: {fid}") from exc
            try:
                record = await svc.get(parsed_id)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"File {fid} not found") from exc
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


def _artifact_step_sort_key(artifact_path: str) -> tuple[int, str]:
    parts = Path(artifact_path).parts
    if parts and parts[0].startswith("step_"):
        try:
            return int(parts[0].split("_", 1)[1]), artifact_path
        except ValueError:
            pass
    return -1, artifact_path


def _resolve_message_named_artifact_paths(conversation_dir: Path, message: str) -> list[str]:
    if not message.strip() or not conversation_dir.exists():
        return []

    artifacts = _list_artifacts(conversation_dir, conversation_id="")
    resolved: list[str] = []
    seen_filenames: set[str] = set()
    for artifact in sorted(artifacts, key=lambda item: _artifact_step_sort_key(item["path"]), reverse=True):
        filename = str(artifact.get("filename", "") or "")
        artifact_path = str(artifact.get("path", "") or "")
        if not filename or not artifact_path or filename in seen_filenames:
            continue
        if filename in message:
            resolved.append(artifact_path)
            seen_filenames.add(filename)
    return resolved


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
    conversation_run_id: str,
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
        "configurable": {"thread_id": conversation_id, "run_id": conversation_run_id},
    }
    if selected_inputs:
        input_state["files"] = selected_inputs
        input_state["current_files"] = selected_paths
    return input_state


def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_agent_stream_error(exc: Exception) -> str:
    if isinstance(exc, (APIConnectionError, APITimeoutError, httpx.ConnectError, httpx.TimeoutException)):
        return (
            "无法连接模型服务。请检查 OpenAI 配置，以及当前进程的 "
            "HTTP_PROXY、HTTPS_PROXY、OPENAI_BASE_URL 是否正确。"
        )
    if isinstance(exc, AuthenticationError):
        return "模型服务鉴权失败。请检查 OPENAI_API_KEY 是否正确。"
    if isinstance(exc, RateLimitError):
        return "模型服务限流。请稍后重试，或检查当前账号/模型配额。"
    if isinstance(exc, BadRequestError):
        return f"模型请求无效：{exc}"
    if isinstance(exc, APIStatusError):
        return f"模型服务请求失败（HTTP {exc.status_code}）。"
    return str(exc) if str(exc).strip() else "处理失败，请查看后端日志。"


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


def _format_tool_label(tool_name: str) -> str:
    return tool_name.replace("_", " ").strip().title() if tool_name else "Tool"


def _tool_client_summary(tool_name: str, parsed_result) -> dict[str, object]:
    meta = parsed_result.meta if parsed_result else {}
    warning = meta.get("warning") if isinstance(meta, dict) and isinstance(meta.get("warning"), str) else ""
    log = parsed_result.log.strip() if parsed_result and parsed_result.log else ""
    return {
        "name": tool_name,
        "label": _format_tool_label(tool_name),
        "message": log,
        "warning": warning,
        "elapsed_seconds": parsed_result.elapsed_seconds if parsed_result else None,
    }


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


def _conversation_stats_cache_path(conversation_dir: Path) -> Path:
    return conversation_dir / _CONVERSATION_STATS_CACHE_FILE


def _scan_conversation_stats(conversation_dir: Path) -> tuple[int, int]:
    step_count = 0
    artifact_count = 0
    for step_dir in conversation_dir.iterdir():
        try:
            if not step_dir.is_dir() or not step_dir.name.startswith("step_"):
                continue
        except OSError:
            continue
        step_count += 1
        artifact_count += sum(1 for candidate in step_dir.rglob("*") if _is_user_visible_artifact(candidate, step_dir))
    return step_count, artifact_count


def _load_conversation_stats(conversation_dir: Path) -> tuple[int, int]:
    stats_file = _conversation_stats_cache_path(conversation_dir)
    now = time.time()
    conversation_mtime_ns = 0
    try:
        conversation_mtime_ns = conversation_dir.stat().st_mtime_ns
    except OSError:
        return (0, 0)

    if settings.conversation_stats_cache_ttl_sec > 0 and stats_file.exists():
        try:
            payload = json.loads(stats_file.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("conversation_mtime_ns") == conversation_mtime_ns
                and isinstance(payload.get("cached_at"), (int, float))
                and (now - float(payload["cached_at"])) <= settings.conversation_stats_cache_ttl_sec
            ):
                step_count = int(payload.get("step_count") or 0)
                artifact_count = int(payload.get("artifact_count") or 0)
                return (step_count, artifact_count)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.debug("Conversation stats cache read failed for %s", conversation_dir, exc_info=True)

    step_count, artifact_count = _scan_conversation_stats(conversation_dir)
    payload = {
        "conversation_mtime_ns": conversation_mtime_ns,
        "cached_at": now,
        "step_count": step_count,
        "artifact_count": artifact_count,
    }
    try:
        stats_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.debug("Conversation stats cache write failed for %s", conversation_dir, exc_info=True)
    return step_count, artifact_count


def _count_artifacts(conversation_dir: Path) -> int:
    return _load_conversation_stats(conversation_dir)[1]


def _is_user_visible_artifact(candidate: Path, step_dir: Path) -> bool:
    try:
        if not candidate.is_file():
            return False
        rel = candidate.relative_to(step_dir)
    except OSError:
        return False

    return all(part and not part.startswith(".") for part in rel.parts)


def _is_state_backend_error(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError)):
        return True
    lowered = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "checkpoint",
        "checkpointer",
        "postgres",
        "connection",
        "network",
        "timeout",
        "database",
        "closed pool",
        "unavailable",
    )
    return any(marker in lowered for marker in markers)


async def _load_conversation_messages_from_graph(conversation_id: str, request: Request) -> list[dict]:
    """Load persisted conversation messages from LangGraph state."""
    graph = request.app.state.graph
    messages: list[dict] = []
    if graph is None:
        raise RuntimeError("graph unavailable")

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
    return messages


async def _load_conversation_messages(
    conversation_id: str,
    request: Request,
    *,
    conversation_dir: Path,
) -> ConversationMessagesLoadResult:
    if request.app.state.graph is None:
        fallback_messages = load_history_messages(conversation_dir)
        warning = "Conversation state backend is unavailable; returned local history only"
        metrics.record_conversation_state_load(source="history", status="degraded")
        return ConversationMessagesLoadResult(
            messages=fallback_messages,
            source="history",
            status="degraded",
            warning=warning,
        )

    try:
        messages = await _load_conversation_messages_from_graph(conversation_id, request)
        metrics.record_conversation_state_load(source="checkpointer", status="ok")
        return ConversationMessagesLoadResult(
            messages=messages,
            source="checkpointer",
            status="ok",
        )
    except Exception as exc:
        if not settings.degrade_on_state_backend_failure or not _is_state_backend_error(exc):
            raise
        logger.warning(
            "Conversation state backend unavailable for %s; fallback to local history",
            conversation_id,
            exc_info=True,
        )
        fallback_messages = load_history_messages(conversation_dir)
        warning = "Conversation state backend unavailable; returned degraded history"
        metrics.record_conversation_state_load(source="history", status="degraded")
        return ConversationMessagesLoadResult(
            messages=fallback_messages,
            source="history",
            status="degraded",
            warning=warning,
        )


def _serialize_conversation(conversation_dir: Path) -> dict:
    stat = conversation_dir.stat()
    step_count, artifact_count = _load_conversation_stats(conversation_dir)
    return {
        "id": conversation_dir.name,
        "conversation_id": conversation_dir.name,
        "title": _read_conversation_title(conversation_dir),
        "created_at": stat.st_ctime,
        "updated_at": stat.st_mtime,
        "artifact_count": artifact_count,
        "step_count": step_count,
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
                if not _is_user_visible_artifact(artifact, step_dir):
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
    state_result = await _load_conversation_messages(
        conversation_id,
        request,
        conversation_dir=conversation_dir,
    )
    payload["messages"] = state_result.messages
    payload["state"] = {
        "source": state_result.source,
        "status": state_result.status,
    }
    if state_result.warning:
        payload["state"]["warning"] = state_result.warning
    return payload


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    """Delete a conversation and its persisted state."""
    conversation_dir = _resolve_conversation_dir(conversation_id)
    if not conversation_dir.exists():
        raise HTTPException(status_code=404, detail="Conversation not found")

    checkpointer = getattr(request.app.state, "checkpointer", None)
    checkpoint_failed = False
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(conversation_id)
        except Exception:
            logger.warning("Failed to delete checkpoint state for %s", conversation_id, exc_info=True)
            checkpoint_failed = True

    shutil.rmtree(conversation_dir)
    result = {"deleted": True, "id": conversation_id}
    if checkpoint_failed:
        result["warning"] = "Checkpoint state could not be removed"
    return result


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


def _idempotency_replay_stream(payload: dict[str, object]):
    async def _stream():
        yield _sse_event("idempotency_replay", payload)
        yield _sse_event("done", {})
    return _stream


@router.post("/api/conversations/{conversation_id}/messages")
async def create_message(conversation_id: str, req: MessageCreateRequest, request: Request):
    """Stream a conversation turn with the LangGraph agent via SSE."""
    graph = request.app.state.graph
    if graph is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    conversation_id = _validate_conversation_id(conversation_id, status_code=422)
    idempotency_key_header = request.headers.get("Idempotency-Key")
    try:
        idempotency_key = normalize_idempotency_key(idempotency_key_header)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    idempotency_record_id = None
    if idempotency_key:
        try:
            decision = await idempotency_service.acquire(
                scope=f"conversation_message:{conversation_id}",
                key=idempotency_key,
                request_hash=build_request_hash(
                    {
                        "conversation_id": conversation_id,
                        "message": req.message,
                        "file_ids": req.file_ids,
                        "artifact_paths": req.artifact_paths,
                    }
                ),
            )
            if decision.action == "conflict":
                raise HTTPException(status_code=409, detail=decision.message or "Idempotency key conflict")
            if decision.action == "in_progress":
                detail: dict[str, object] = {
                    "detail": "A request with the same Idempotency-Key is already in progress",
                }
                if decision.response_payload:
                    detail["existing"] = decision.response_payload
                raise HTTPException(status_code=409, detail=detail)
            if decision.action == "replay":
                replay_payload = decision.response_payload or {"conversation_id": conversation_id, "status": "REPLAYED"}
                return StreamingResponse(
                    _idempotency_replay_stream(replay_payload)(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                        "X-Idempotency-Replayed": "true",
                    },
                )
            idempotency_record_id = decision.record_id
        except HTTPException:
            raise
        except Exception:
            logger.warning(
                "Idempotency backend unavailable for conversation message; request continues without dedupe",
                exc_info=True,
            )
            idempotency_record_id = None
            idempotency_key = None

    conversation_run_id = f"{conversation_id}:{uuid.uuid4().hex}"

    # Resolve uploaded files
    uploaded_files = await _resolve_uploaded_files(req.file_ids)
    # Create conversation workdir only after request validation succeeds
    conversation_workdir = _resolve_conversation_dir(conversation_id)
    conversation_workdir.mkdir(parents=True, exist_ok=True)
    message_named_artifact_paths = _resolve_message_named_artifact_paths(conversation_workdir, req.message)
    effective_artifact_paths = message_named_artifact_paths or req.artifact_paths
    selected_artifacts = _resolve_selected_artifacts(conversation_id, effective_artifact_paths)
    selected_inputs = (selected_artifacts + uploaded_files) if message_named_artifact_paths else (uploaded_files + selected_artifacts)
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

    append_history_message(
        conversation_dir=conversation_workdir,
        msg_type="human",
        content=req.message,
        attachments=selected_input_summaries or None,
    )

    input_state = _build_message_input_state(
        message=req.message,
        human_message_kwargs=human_message_kwargs,
        conversation_workdir=conversation_workdir,
        conversation_id=conversation_id,
        conversation_run_id=conversation_run_id,
        selected_inputs=selected_inputs,
    )

    config = {"configurable": {"thread_id": conversation_id}}

    if idempotency_record_id is not None:
        try:
            await idempotency_service.mark_processing(
                record_id=idempotency_record_id,
                response_payload={
                    "conversation_id": conversation_id,
                    "conversation_run_id": conversation_run_id,
                    "status": "PROCESSING",
                },
            )
        except Exception:
            logger.warning("Failed to persist idempotency processing state for %s", conversation_run_id, exc_info=True)

    async def event_stream():
        yield _sse_event("conversation", {"conversation_id": conversation_id})

        # Import progress queue helpers
        from pdf_agent.agent.tools_adapter import get_progress_queue, release_progress_queue
        progress_queue = get_progress_queue(conversation_run_id)
        stream_iter = graph.astream_events(input_state, config=config, version="v2").__aiter__()
        current_tool_name = ""
        started_at = time.perf_counter()
        last_heartbeat_at = started_at
        run_status = "SUCCESS"
        assistant_chunks: list[str] = []
        assistant_artifacts: list[str] = []
        stream_error_message = ""

        def drain_progress_updates() -> list[dict[str, object]]:
            updates: list[dict[str, object]] = []
            while True:
                try:
                    item = progress_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, dict):
                    updates.append(item)
            return updates

        try:
            while True:
                try:
                    event = await asyncio.wait_for(stream_iter.__anext__(), timeout=0.25)
                except asyncio.TimeoutError:
                    for update in drain_progress_updates():
                        yield _sse_event("progress", {
                            "name": current_tool_name,
                            "label": _format_tool_label(current_tool_name),
                            "percent": update.get("percent"),
                            "message": update.get("message", ""),
                        })
                        last_heartbeat_at = time.perf_counter()
                    if current_tool_name and time.perf_counter() - last_heartbeat_at >= _HEARTBEAT_INTERVAL:
                        yield _sse_event("heartbeat", {
                            "name": current_tool_name,
                            "label": _format_tool_label(current_tool_name),
                        })
                        last_heartbeat_at = time.perf_counter()
                    continue
                except StopAsyncIteration:
                    break

                kind = event["event"]

                # LLM token streaming
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                        assistant_chunks.append(text)
                        yield _sse_event("token", {"content": text})

                elif kind == "on_tool_start":
                    current_tool_name = str(event.get("name") or "tool")
                    tool_input = event.get("data", {}).get("input", {})
                    yield _sse_event("tool_start", {
                        "name": current_tool_name,
                        "label": _format_tool_label(current_tool_name),
                        "args": _sanitize_tool_args(tool_input) if isinstance(tool_input, dict) else {},
                    })
                    last_heartbeat_at = time.perf_counter()

                # Tool end — only expose output files, not internal tool details
                elif kind == "on_tool_end":
                    for update in drain_progress_updates():
                        yield _sse_event("progress", {
                            "name": current_tool_name,
                            "label": _format_tool_label(current_tool_name),
                            "percent": update.get("percent"),
                            "message": update.get("message", ""),
                        })
                    output = event["data"].get("output", "")
                    parsed = parse_tool_result_payload(output) if isinstance(output, str) else None
                    file_paths = parsed.output_files if parsed else _extract_output_files(output)
                    if file_paths:
                        artifact_urls = _paths_to_download_urls(conversation_id, file_paths)
                        assistant_artifacts.extend(artifact_urls)
                        yield _sse_event("artifact", {
                            "files": artifact_urls,
                        })
                    tool_name = str(event.get("name") or current_tool_name or "tool")
                    yield _sse_event("tool_end", _tool_client_summary(tool_name, parsed))
                    current_tool_name = ""
                    last_heartbeat_at = time.perf_counter()

        except asyncio.CancelledError:
            run_status = "CANCELLED"
            terminated = cancel_conversation_processes(conversation_run_id)
            if terminated:
                logger.info(
                    "Cancelled %d subprocess(es) for conversation run %s",
                    terminated,
                    conversation_run_id,
                )
            raise
        except Exception as e:
            run_status = "ERROR"
            stream_error_message = _format_agent_stream_error(e)
            terminated = cancel_conversation_processes(conversation_run_id)
            if terminated:
                logger.info(
                    "Stopped %d subprocess(es) after stream error for run %s",
                    terminated,
                    conversation_run_id,
                )
            logger.exception("Agent stream error")
            yield _sse_event("error", {"message": stream_error_message})
        finally:
            metrics.record_conversation_run(
                status=run_status,
                duration=time.perf_counter() - started_at,
            )
            deduped_artifacts = list(dict.fromkeys(assistant_artifacts))
            if run_status == "SUCCESS":
                assistant_text = "".join(assistant_chunks).strip()
                if assistant_text or deduped_artifacts:
                    append_history_message(
                        conversation_dir=conversation_workdir,
                        msg_type="ai",
                        content=assistant_text,
                        files=deduped_artifacts or None,
                    )
            elif run_status == "ERROR":
                append_history_message(
                    conversation_dir=conversation_workdir,
                    msg_type="system",
                    content=stream_error_message or "处理失败，请查看后端日志",
                    meta={"status": "ERROR"},
                )
            elif run_status == "CANCELLED":
                append_history_message(
                    conversation_dir=conversation_workdir,
                    msg_type="system",
                    content="请求已取消",
                    meta={"status": "CANCELLED"},
                )

            if idempotency_record_id is not None:
                try:
                    if run_status == "SUCCESS":
                        await idempotency_service.mark_succeeded(
                            record_id=idempotency_record_id,
                            response_code=200,
                            response_payload={
                                "conversation_id": conversation_id,
                                "conversation_run_id": conversation_run_id,
                                "status": run_status,
                                "artifacts": deduped_artifacts,
                            },
                        )
                    else:
                        await idempotency_service.mark_failed(
                            record_id=idempotency_record_id,
                            response_code=409 if run_status == "CANCELLED" else 500,
                            error_message=stream_error_message or run_status,
                            response_payload={
                                "conversation_id": conversation_id,
                                "conversation_run_id": conversation_run_id,
                                "status": run_status,
                            },
                        )
                except Exception:
                    logger.warning(
                        "Failed to persist idempotency final state for run %s",
                        conversation_run_id,
                        exc_info=True,
                    )
            release_progress_queue(conversation_run_id)

        yield _sse_event("done", {})

    response_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if idempotency_key:
        response_headers["X-Idempotency-Key"] = idempotency_key
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
