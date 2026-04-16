"""Filesystem-backed conversation message history for degraded state fallback."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_FILE = ".history.jsonl"


def history_file_path(conversation_dir: Path) -> Path:
    return conversation_dir / _HISTORY_FILE


def append_history_message(
    *,
    conversation_dir: Path,
    msg_type: str,
    content: str,
    attachments: list[dict[str, Any]] | None = None,
    files: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "type": msg_type,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if attachments:
        payload["attachments"] = attachments
    if files:
        payload["files"] = files
    if meta:
        payload["meta"] = meta

    path = history_file_path(conversation_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str))
            fh.write("\n")
    except OSError:
        logger.warning("Failed to append conversation history at %s", path, exc_info=True)


def load_history_messages(conversation_dir: Path) -> list[dict[str, Any]]:
    path = history_file_path(conversation_dir)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed history line in %s", path)
                    continue
                if not isinstance(item, dict):
                    continue
                msg_type = item.get("type")
                content = item.get("content", "")
                if not isinstance(msg_type, str):
                    continue
                if not isinstance(content, str):
                    content = str(content)
                message: dict[str, Any] = {"type": msg_type, "content": content}
                attachments = item.get("attachments")
                if isinstance(attachments, list):
                    message["attachments"] = [
                        entry for entry in attachments if isinstance(entry, dict)
                    ]
                files = item.get("files")
                if isinstance(files, list):
                    message["files"] = [path for path in files if isinstance(path, str) and path]
                messages.append(message)
    except OSError:
        logger.warning("Failed to read conversation history at %s", path, exc_info=True)
        return []
    return messages
