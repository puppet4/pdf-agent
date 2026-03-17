"""Files API - upload and download files."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.db import get_session
from pdf_agent.schemas.file import FileUploadResponse
from pdf_agent.services import FileService

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("", response_model=FileUploadResponse)
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


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Download an uploaded file."""
    from fastapi.responses import FileResponse

    svc = FileService(session)
    record = await svc.get(file_id)
    from pathlib import Path

    path = Path(record.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path, filename=record.orig_name, media_type=record.mime_type)
