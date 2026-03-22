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

    def create_conversation_workdir(self, conversation_id: str) -> Path:
        workdir = settings.conversations_dir / conversation_id
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def create_conversation_step_dir(self, conversation_id: str, step: int) -> Path:
        step_dir = settings.conversations_dir / conversation_id / f"step_{step}"
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

    def cleanup_conversation(self, conversation_id: str) -> None:
        conversation_dir = settings.conversations_dir / conversation_id
        if conversation_dir.exists():
            shutil.rmtree(conversation_dir)

    def list_expired_conversations(self) -> list[str]:
        """Return conversation ids whose workdirs are older than the retention window."""
        conversations_dir = settings.conversations_dir
        if not conversations_dir.exists():
            return []

        cutoff = time.time() - settings.conversation_ttl_hours * 3600
        expired: list[str] = []
        for entry in conversations_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    expired.append(entry.name)
            except OSError:
                logger.warning("Failed to inspect conversation workdir: %s", entry.name)
        return expired

    def cleanup_expired_conversations(self) -> int:
        """Remove expired conversation workdirs. Returns count removed."""
        removed = 0
        for conversation_id in self.list_expired_conversations():
            try:
                self.cleanup_conversation(conversation_id)
                removed += 1
                logger.info("Cleaned up expired conversation workdir: %s", conversation_id)
            except OSError:
                logger.warning("Failed to clean up conversation workdir: %s", conversation_id)
        return removed

    def cleanup_expired_uploads(self) -> list[str]:
        """Remove uploaded files older than the retention window and return removed upload ids."""
        upload_dir = settings.upload_dir
        if not upload_dir.exists():
            return []

        cutoff = time.time() - settings.conversation_ttl_hours * 3600
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
        """Delete oldest upload/conversation dirs first until under the configured storage limit."""
        limit = self.storage_limit_bytes()
        current = self.dir_size_bytes()
        if current <= limit:
            return 0

        removed = 0
        candidates = []
        for root in (settings.conversations_dir, settings.upload_dir):
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
