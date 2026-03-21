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
        safe_name = Path(filename).name or "upload.bin"
        dest = dest_dir / safe_name
        dest.write_bytes(content)
        return dest

    def save_upload_from_path(self, file_id: uuid.UUID, filename: str, source_path: Path) -> Path:
        """Persist a prepared upload file from a temporary location."""
        dest_dir = settings.upload_dir / str(file_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name or "upload.bin"
        dest = dest_dir / safe_name
        shutil.copy2(source_path, dest)
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

    @staticmethod
    def compute_sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def cleanup_thread(self, thread_id: str) -> None:
        thread_dir = settings.threads_dir / thread_id
        if thread_dir.exists():
            shutil.rmtree(thread_dir)

    def cleanup_execution(self, execution_id: str) -> None:
        execution_dir = settings.executions_dir / execution_id
        if execution_dir.exists():
            shutil.rmtree(execution_dir)

    def list_expired_threads(self) -> list[str]:
        """Return thread ids whose workdirs are older than thread_ttl_hours."""
        threads_dir = settings.threads_dir
        if not threads_dir.exists():
            return []

        cutoff = time.time() - settings.thread_ttl_hours * 3600
        expired: list[str] = []
        for entry in threads_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    expired.append(entry.name)
            except OSError:
                logger.warning("Failed to inspect thread workdir: %s", entry.name)
        return expired

    def cleanup_expired_threads(self) -> int:
        """Remove thread workdirs older than thread_ttl_hours. Returns count removed."""
        removed = 0
        for thread_id in self.list_expired_threads():
            try:
                self.cleanup_thread(thread_id)
                removed += 1
                logger.info("Cleaned up expired thread workdir: %s", thread_id)
            except OSError:
                logger.warning("Failed to clean up thread workdir: %s", thread_id)
        return removed

    def cleanup_expired_uploads(self) -> list[str]:
        """Remove uploaded files older than thread_ttl_hours and return removed upload ids."""
        upload_dir = settings.upload_dir
        if not upload_dir.exists():
            return []

        cutoff = time.time() - settings.thread_ttl_hours * 3600
        removed: list[str] = []
        for entry in upload_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(entry)
                    removed.append(entry.name)
                    logger.info("Cleaned up expired upload: %s", entry.name)
            except OSError:
                logger.warning("Failed to clean up upload: %s", entry.name)
        return removed

    def list_expired_executions(self) -> list[str]:
        executions_dir = settings.executions_dir
        if not executions_dir.exists():
            return []
        cutoff = time.time() - settings.job_ttl_hours * 3600
        expired: list[str] = []
        for entry in executions_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    expired.append(entry.name)
            except OSError:
                logger.warning("Failed to inspect execution workdir: %s", entry.name)
        return expired

    def dir_size_bytes(self, root: Path | None = None) -> int:
        total = 0
        base = root or settings.data_dir
        if not base.exists():
            return 0
        for path in base.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    logger.warning("Failed to stat file during size scan: %s", path)
        return total

    def storage_limit_bytes(self) -> int:
        return settings.max_storage_gb * 1024 * 1024 * 1024

    def trim_storage_lru(self) -> int:
        """Delete oldest execution dirs first until under the configured storage limit."""
        limit = self.storage_limit_bytes()
        current = self.dir_size_bytes()
        if current <= limit:
            return 0

        removed = 0
        candidates = []
        for root in (settings.executions_dir, settings.upload_dir):
            if not root.exists():
                continue
            for entry in root.iterdir():
                if entry.is_dir():
                    try:
                        candidates.append((entry.stat().st_mtime, entry))
                    except OSError:
                        continue
        for _, entry in sorted(candidates, key=lambda item: item[0]):
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
            current = self.dir_size_bytes()
            if current <= limit:
                break
        return removed


storage = LocalStorage()
