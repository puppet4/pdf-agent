"""Local file storage management."""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
import uuid
from pathlib import Path

from pdf_agent.config import settings

logger = logging.getLogger(__name__)


class LocalStorage:
    """Manages file storage on local disk."""

    def save_upload(self, file_id: uuid.UUID, filename: str, content: bytes) -> Path:
        """Save an uploaded file and return its storage path."""
        dest_dir = settings.upload_dir / str(file_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        dest.write_bytes(content)
        return dest

    def get_upload_path(self, file_id: uuid.UUID, filename: str) -> Path:
        return settings.upload_dir / str(file_id) / filename

    def create_thread_workdir(self, thread_id: str) -> Path:
        workdir = settings.threads_dir / thread_id
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def create_thread_step_dir(self, thread_id: str, step: int) -> Path:
        step_dir = settings.threads_dir / thread_id / f"step_{step}"
        step_dir.mkdir(parents=True, exist_ok=True)
        return step_dir

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def cleanup_thread(self, thread_id: str) -> None:
        thread_dir = settings.threads_dir / thread_id
        if thread_dir.exists():
            shutil.rmtree(thread_dir)

    def cleanup_expired_threads(self) -> int:
        """Remove thread workdirs older than thread_ttl_hours. Returns count removed."""
        threads_dir = settings.threads_dir
        if not threads_dir.exists():
            return 0

        cutoff = time.time() - settings.thread_ttl_hours * 3600
        removed = 0
        for entry in threads_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(entry)
                    removed += 1
                    logger.info("Cleaned up expired thread workdir: %s", entry.name)
            except OSError:
                logger.warning("Failed to clean up thread workdir: %s", entry.name)
        return removed


storage = LocalStorage()
