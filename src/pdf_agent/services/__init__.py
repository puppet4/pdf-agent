"""File service - handles file upload and retrieval with validation."""
from __future__ import annotations

import io
import logging
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import pikepdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.config import settings
from pdf_agent.db.models import FileRecord
from pdf_agent.external_commands import run_command
from pdf_agent.storage import storage

logger = logging.getLogger(__name__)

# Magic byte signatures for file type validation
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "image/tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
    "image/bmp": [b"BM"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [b"PK\x03\x04"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [b"PK\x03\x04"],
    # Legacy Office (OLE2)
    "application/msword": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    "application/vnd.ms-excel": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
    "application/vnd.ms-powerpoint": [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],
}

_OOXML_PREFIXES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word/",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xl/",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt/",
}


def _validate_magic_bytes(content: bytes, declared_mime: str) -> bool:
    """Check if file content matches the declared MIME type's magic bytes."""
    if declared_mime == "image/webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    sigs = _MAGIC_SIGNATURES.get(declared_mime)
    if not sigs:
        return False
    return any(content[:len(sig)] == sig for sig in sigs)


def _validate_office_container(content: bytes, declared_mime: str) -> bool:
    """Validate OOXML ZIP contents so arbitrary ZIP files cannot masquerade as Office docs."""
    required_prefix = _OOXML_PREFIXES.get(declared_mime)
    if not required_prefix:
        return True
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return any(name.startswith(required_prefix) for name in zf.namelist())
    except zipfile.BadZipFile:
        return False


def _validate_declared_content(content: bytes, declared_mime: str) -> bool:
    """Validate both magic bytes and container structure where applicable."""
    return _validate_magic_bytes(content, declared_mime) and _validate_office_container(content, declared_mime)


def _validate_declared_content_path(path: Path, declared_mime: str) -> bool:
    with path.open("rb") as fh:
        header = fh.read(64)
    if not _validate_magic_bytes(header if declared_mime != "image/webp" else path.read_bytes()[:16], declared_mime):
        return False
    required_prefix = _OOXML_PREFIXES.get(declared_mime)
    if not required_prefix:
        return True
    try:
        with zipfile.ZipFile(path) as zf:
            return any(name.startswith(required_prefix) for name in zf.namelist())
    except zipfile.BadZipFile:
        return False


def _generate_thumbnail(pdf_path: Path, thumb_path: Path, size: int = 200) -> bool:
    """Generate a thumbnail for the first page of a PDF using poppler pdftoppm."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return False
    try:
        result = run_command(
            [pdftoppm, "-r", "72", "-jpeg", "-f", "1", "-l", "1", "-scale-to", str(size),
             str(pdf_path), str(thumb_path.with_suffix(""))],
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="ignore").strip() or "unknown error"
            logger.warning("pdftoppm failed while generating thumbnail for %s: %s", pdf_path, detail)
            return False
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
        logger.warning("Thumbnail generation failed for %s", pdf_path, exc_info=True)
        return False


class FileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upload(self, filename: str, content_type: str, content: bytes) -> FileRecord:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, dir=settings.data_dir) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        try:
            return await self.upload_from_path(
                filename=filename,
                content_type=content_type,
                temp_path=temp_path,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    async def upload_from_path(self, filename: str, content_type: str, temp_path: Path) -> FileRecord:
        storage.trim_storage_lru()
        if storage.dir_size_bytes() > storage.storage_limit_bytes():
            raise PDFAgentError(
                ErrorCode.STORAGE_LIMIT_EXCEEDED,
                f"Storage exceeds configured limit of {settings.max_storage_gb}GB",
            )

        # Validate size
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        size_bytes = temp_path.stat().st_size
        if size_bytes > max_bytes:
            raise PDFAgentError(ErrorCode.FILE_TOO_LARGE, f"File exceeds {settings.max_upload_size_mb}MB limit")

        # Validate magic bytes
        if not _validate_declared_content_path(temp_path, content_type):
            raise PDFAgentError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"File content does not match declared type '{content_type}'. Possible file corruption or extension mismatch.",
            )

        file_id = uuid.uuid4()
        page_count = None
        if content_type == "application/pdf":
            try:
                with pikepdf.open(temp_path) as pdf:
                    page_count = len(pdf.pages)
                    if page_count > settings.max_page_count:
                        raise PDFAgentError(
                            ErrorCode.PAGE_COUNT_EXCEEDED,
                            f"PDF exceeds {settings.max_page_count} page limit",
                        )
            except PDFAgentError:
                raise
            except Exception as exc:
                raise PDFAgentError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    "PDF is corrupt or unreadable",
                ) from exc

        sha256 = storage.compute_sha256_file(temp_path)
        path = storage.save_upload_from_path(file_id, filename, temp_path)

        # Generate thumbnail for PDFs
        thumb_path = path.parent / "thumbnail.jpg"
        if content_type == "application/pdf":
            _generate_thumbnail(path, thumb_path)

        record = FileRecord(
            id=file_id,
            orig_name=filename,
            mime_type=content_type,
            size_bytes=size_bytes,
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
