"""Files API - upload, list, download files."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.db import get_session
from pdf_agent.db.models import FileRecord
from pdf_agent.schemas.file import FileUploadResponse
from pdf_agent.services import FileService

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get(
    "",
    summary="List uploaded files",
    description="Returns all uploaded files ordered by most recent first.",
)
async def list_files(session: AsyncSession = Depends(get_session)) -> dict:
    """List all uploaded files."""
    result = await session.execute(
        select(FileRecord).order_by(FileRecord.created_at.desc())
    )
    records = result.scalars().all()
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
    return {"files": files, "count": len(files)}


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
    content = await file.read()
    svc = FileService(session)
    record = await svc.upload(
        filename=file.filename or "unknown",
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )
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
    import shutil
    result = await session.execute(select(FileRecord).where(FileRecord.id == file_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    file_dir = Path(record.storage_path).parent
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)
    await session.delete(record)
    await session.commit()
    return {"deleted": True, "id": str(file_id)}


@router.get(
    "/{file_id}/download",
    summary="Download an uploaded file",
)
async def download_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Download an uploaded file."""
    svc = FileService(session)
    record = await svc.get(file_id)
    path = Path(record.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path, filename=record.orig_name, media_type=record.mime_type)


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

    # Check cache: page images stored alongside the file
    page_cache = pdf_path.parent / f"page_{page}.jpg"
    if page_cache.exists():
        return FileResponse(page_cache, media_type="image/jpeg")

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise HTTPException(status_code=503, detail="pdftoppm not installed")

    with tempfile.TemporaryDirectory() as td:
        out_stem = Path(td) / "page"
        result = subprocess.run(
            [pdftoppm, "-r", "96", "-jpeg", "-f", str(page), "-l", str(page),
             "-scale-to", "400", str(pdf_path), str(out_stem)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="ignore").strip() or "unknown error"
            raise HTTPException(status_code=500, detail=f"pdftoppm failed: {detail}")
        candidates = list(Path(td).glob("*.jpg"))
        if not candidates:
            raise HTTPException(status_code=500, detail="Failed to render page")
        import shutil as _shutil
        _shutil.copy(candidates[0], page_cache)

    return FileResponse(page_cache, media_type="image/jpeg")


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
