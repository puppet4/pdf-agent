"""File service - handles file upload and retrieval."""
from __future__ import annotations

import uuid
from pathlib import Path

import pikepdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.config import settings
from pdf_agent.db.models import FileRecord
from pdf_agent.storage import storage


class FileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upload(self, filename: str, content_type: str, content: bytes) -> FileRecord:
        # Validate size
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise PDFAgentError(ErrorCode.FILE_TOO_LARGE, f"File exceeds {settings.max_upload_size_mb}MB limit")

        file_id = uuid.uuid4()
        sha256 = storage.compute_sha256(content)
        path = storage.save_upload(file_id, filename, content)

        # Try to get page count for PDFs
        page_count = None
        if content_type == "application/pdf":
            try:
                with pikepdf.open(path) as pdf:
                    page_count = len(pdf.pages)
            except Exception:
                pass

        record = FileRecord(
            id=file_id,
            orig_name=filename,
            mime_type=content_type,
            size_bytes=len(content),
            sha256=sha256,
            page_count=page_count,
            storage_path=str(path),
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get(self, file_id: uuid.UUID) -> FileRecord:
        result = await self.session.execute(select(FileRecord).where(FileRecord.id == file_id))
        record = result.scalar_one_or_none()
        if not record:
            raise PDFAgentError(ErrorCode.FILE_NOT_FOUND, f"File {file_id} not found")
        return record

    async def get_path(self, file_id: uuid.UUID) -> Path:
        record = await self.get(file_id)
        return Path(record.storage_path)
