"""Local file storage management."""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from pdf_agent.config import settings


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

    def create_job_workdir(self, job_id: uuid.UUID) -> Path:
        workdir = settings.jobs_dir / str(job_id) / "work"
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def create_job_output_dir(self, job_id: uuid.UUID) -> Path:
        outdir = settings.jobs_dir / str(job_id) / "output"
        outdir.mkdir(parents=True, exist_ok=True)
        return outdir

    def get_job_output_dir(self, job_id: uuid.UUID) -> Path:
        return settings.jobs_dir / str(job_id) / "output"

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def cleanup_job(self, job_id: uuid.UUID) -> None:
        job_dir = settings.jobs_dir / str(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)


storage = LocalStorage()
