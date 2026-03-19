"""File service - handles file upload and retrieval with validation."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pikepdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.config import settings
from pdf_agent.db.models import FileRecord
from pdf_agent.storage import storage

# Magic byte signatures for file type validation
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "image/webp": [b"RIFF"],  # RIFF....WEBP
    "image/tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
    "image/bmp": [b"BM"],
    # Office formats (ZIP-based: docx, xlsx, pptx)
    "application/zip": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [b"PK\x03\x04"],
    # Legacy Office (OLE2)
    "application/msword": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    "application/vnd.ms-excel": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    "application/vnd.ms-powerpoint": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
}


def _validate_magic_bytes(content: bytes, declared_mime: str) -> bool:
    """Check if file content matches the declared MIME type's magic bytes."""
    sigs = _MAGIC_SIGNATURES.get(declared_mime)
    if not sigs:
        return True  # unknown type → allow (no signature to check)
    return any(content[:len(sig)] == sig for sig in sigs)


def _generate_thumbnail(pdf_path: Path, thumb_path: Path, size: int = 200) -> bool:
    """Generate a thumbnail for the first page of a PDF using poppler pdftoppm."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return False
    try:
        subprocess.run(
            [pdftoppm, "-r", "72", "-jpeg", "-f", "1", "-l", "1", "-scale-to", str(size),
             str(pdf_path), str(thumb_path.with_suffix(""))],
            capture_output=True, timeout=30,
        )
        # pdftoppm outputs file as <stem>-1.jpg
        candidate = thumb_path.with_name(thumb_path.stem + "-1.jpg")
        if candidate.exists():
            candidate.rename(thumb_path)
            return True
        # Try without page suffix
        if thumb_path.exists():
            return True
        return False
    except Exception:
        return False


class FileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upload(self, filename: str, content_type: str, content: bytes) -> FileRecord:
        # Validate size
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise PDFAgentError(ErrorCode.FILE_TOO_LARGE, f"File exceeds {settings.max_upload_size_mb}MB limit")

        # Validate magic bytes
        if not _validate_magic_bytes(content, content_type):
            raise PDFAgentError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"File content does not match declared type '{content_type}'. Possible file corruption or extension mismatch.",
            )

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

        # Generate thumbnail for PDFs
        thumb_path = path.parent / "thumbnail.jpg"
        if content_type == "application/pdf":
            _generate_thumbnail(path, thumb_path)

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
