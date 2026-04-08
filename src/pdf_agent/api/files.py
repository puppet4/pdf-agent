"""Files API - upload, list, download files."""
from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.api.http import content_disposition_headers
from pdf_agent.db import get_session
from pdf_agent.db.models import FileRecord
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.file import FileUploadResponse
from pdf_agent.services import FilePersistenceError, FileService, load_storage_record

router = APIRouter(prefix="/api/files", tags=["files"])
logger = logging.getLogger(__name__)
_content_disposition_headers = content_disposition_headers

def _normalize_upload_content_type(filename: str, content_type: str | None) -> str:
    """Prefer the browser MIME type, but recover known types from filename when generic."""
    normalized = (content_type or "").strip().lower()
    if normalized and normalized != "application/octet-stream":
        return normalized
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _spill_upload_to_tempfile(file: UploadFile, tmp_path: Path | None = None) -> Path:
    """Stream uploads to a temporary file so under-limit files are not buffered in memory."""
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    temp_root = tmp_path or (settings.data_dir / "tmp_uploads")
    temp_root.mkdir(parents=True, exist_ok=True)
    total = 0
    with tempfile.NamedTemporaryFile(delete=False, dir=temp_root) as tmp:
        temp_file = Path(tmp.name)
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PDFAgentError(
                        ErrorCode.FILE_TOO_LARGE,
                        f"File exceeds {settings.max_upload_size_mb}MB limit",
                    )
                tmp.write(chunk)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise
    return temp_file


@router.get(
    "",
    summary="List uploaded files",
    description="Returns all uploaded files ordered by most recent first.",
)
async def list_files(
    page: int = 1,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _list_files_impl(page=page, limit=limit, session=session)


async def _list_files_impl(page: int, limit: int, session: AsyncSession) -> dict:
    """List all uploaded files."""
    records = await FileService(session).list_records()
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    total = len(records)
    start = (page - 1) * limit
    end = start + limit
    records = records[start:end]
    files = [
        {
            "id": str(r.id),
            "orig_name": r.orig_name,
            "mime_type": r.mime_type,
            "size_bytes": r.size_bytes,
            "page_count": r.page_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": f"/api/files/{r.id}/download",
            "thumbnail_url": f"/api/files/{r.id}/thumbnail" if r.mime_type == "application/pdf" else None,
        }
        for r in records
    ]
    return {"files": files, "count": len(files), "total": total, "page": page, "limit": limit}

@router.post(
    "",
    response_model=FileUploadResponse,
    summary="Upload a file",
    description="Upload a PDF, image, or Office document. Returns file metadata including id for use with tools.",
)
async def upload_file(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> FileUploadResponse:
    """Upload a file (PDF, image, Office doc, etc.)."""
    svc = FileService(session)
    temp_path = await _spill_upload_to_tempfile(file)
    try:
        record = await svc.upload_from_path(
            filename=file.filename or "unknown",
            content_type=_normalize_upload_content_type(
                file.filename or "unknown",
                file.content_type,
            ),
            temp_path=temp_path,
        )
    except FilePersistenceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return FileUploadResponse(
        id=record.id,
        orig_name=record.orig_name,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        page_count=record.page_count,
        created_at=record.created_at,
    )


@router.delete(
    "/{file_id}",
    summary="Delete an uploaded file",
)
async def delete_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Delete an uploaded file from DB and disk."""
    svc = FileService(session)
    try:
        record = await svc.get(file_id)
    except PDFAgentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    file_dir = Path(record.storage_path).parent
    persisted = False
    try:
        result = await session.execute(select(FileRecord).where(FileRecord.id == file_id))
        persisted_record = result.scalar_one_or_none()
        if persisted_record is not None:
            await session.delete(persisted_record)
            await session.commit()
            persisted = True
    except Exception:
        await session.rollback()
        logger.warning("Failed to delete DB record for %s; removing storage only", file_id, exc_info=True)
    if file_dir.exists():
        try:
            shutil.rmtree(file_dir, ignore_errors=False)
        except OSError:
            logger.warning("Failed to remove upload directory for %s", file_id, exc_info=True)
    if not persisted and load_storage_record(file_id) is not None:
        raise HTTPException(status_code=500, detail="Failed to remove file storage")
    return {"deleted": True, "id": str(file_id)}


@router.get(
    "/{file_id}/download",
    summary="Download an uploaded file",
)
async def download_file(
    file_id: uuid.UUID,
    inline: bool = Query(False, description="Return with inline Content-Disposition for preview"),
    session: AsyncSession = Depends(get_session),
):
    """Download an uploaded file."""
    svc = FileService(session)
    record = await svc.get(file_id)
    path = Path(record.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path,
        filename=record.orig_name,
        media_type=record.mime_type,
        headers=content_disposition_headers(record.orig_name, inline=inline),
    )


@router.get(
    "/{file_id}/pages/{page}",
    summary="Get a specific PDF page as image",
    description="Returns a JPG image of the specified page (1-indexed) of an uploaded PDF.",
)
async def get_page_image(
    file_id: uuid.UUID,
    page: int,
    session: AsyncSession = Depends(get_session),
):
    """Render a specific PDF page as a JPG thumbnail."""
    svc = FileService(session)
    record = await svc.get(file_id)

    if record.mime_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files support page preview")

    if record.page_count and (page < 1 or page > record.page_count):
        raise HTTPException(status_code=400, detail=f"Page {page} out of range (1-{record.page_count})")

    pdf_path = Path(record.storage_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise HTTPException(status_code=503, detail="pdftoppm not installed")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    render_dir = Path(tempfile.mkdtemp(prefix="page-preview-", dir=settings.data_dir))
    out_stem = render_dir / "page"
    result = run_command(
        [pdftoppm, "-r", "96", "-jpeg", "-f", str(page), "-l", str(page),
         "-scale-to", "400", str(pdf_path), str(out_stem)],
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        shutil.rmtree(render_dir, ignore_errors=True)
        detail = result.stderr.decode("utf-8", errors="ignore").strip() or "unknown error"
        raise HTTPException(status_code=500, detail=f"pdftoppm failed: {detail}")
    candidates = list(render_dir.glob("*.jpg"))
    if not candidates:
        shutil.rmtree(render_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Failed to render page")

    return FileResponse(
        candidates[0],
        media_type="image/jpeg",
        background=BackgroundTask(shutil.rmtree, render_dir, True),
    )


@router.get(
    "/{file_id}/thumbnail",
    summary="Get PDF thumbnail",
    description="Returns a JPG thumbnail of the first page of an uploaded PDF.",
)
async def get_thumbnail(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Return the thumbnail image for an uploaded PDF (JPG)."""
    svc = FileService(session)
    record = await svc.get(file_id)
    thumb_path = Path(record.storage_path).parent / "thumbnail.jpg"
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    return FileResponse(thumb_path, media_type="image/jpeg")
