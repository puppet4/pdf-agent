"""文件 API，负责上传、列出、下载和预览用户文件。"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.api.http import content_disposition_headers
from pdf_agent.api.metrics import metrics
from pdf_agent.db import get_session
from pdf_agent.db.models import FileRecord
from pdf_agent.external_commands import run_command
from pdf_agent.schemas.file import FileUploadResponse
from pdf_agent.services import FilePersistenceError, FileService, load_storage_record
from pdf_agent.services.idempotency import (
    build_request_hash,
    hash_idempotency_key,
    idempotency_service,
    normalize_idempotency_key,
)
from pdf_agent.storage import storage

router = APIRouter(prefix="/api/files", tags=["files"])
logger = logging.getLogger(__name__)


def _resolve_storage_path(record_storage_path: str) -> Path:
    """解析并校验存储路径，确保目标文件位于上传目录内。"""
    path = Path(record_storage_path).resolve()
    upload_root = settings.upload_dir.resolve()
    if not path.is_relative_to(upload_root):
        raise HTTPException(status_code=500, detail="Storage path validation failed")
    return path
_content_disposition_headers = content_disposition_headers

def _normalize_upload_content_type(filename: str, content_type: str | None) -> str:
    """优先使用浏览器上报的 MIME 类型，必要时再根据文件名兜底推断。"""
    normalized = (content_type or "").strip().lower()
    if normalized and normalized != "application/octet-stream":
        return normalized
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def _spill_upload_to_tempfile(file: UploadFile, tmp_path: Path | None = None) -> Path:
    """把上传流边读边写入临时文件，避免整文件常驻内存。

    这样后续文件校验、SHA256 计算和真正落盘都可以围绕临时路径进行，
    同时也能在超出大小限制时尽早中断，不必把整个文件吃进内存。
    """
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
    """分页列出上传文件，并补齐下载与缩略图访问地址。"""
    svc = FileService(session)
    page = max(1, int(page))
    limit = max(1, min(int(limit), 200))
    total = await svc.count_records()
    records = await svc.list_records_paginated(page, limit)
    files = [
        {
            "id": str(r.id),
            "orig_name": r.orig_name,
            "mime_type": r.mime_type,
            "size_bytes": r.size_bytes,
            "page_count": r.page_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "download_url": f"/api/files/{r.id}/download",
            "thumbnail_url": (
                f"/api/files/{r.id}/thumbnail"
                if r.mime_type == "application/pdf" and Path(r.storage_path).parent.joinpath("thumbnail.jpg").exists()
                else None
            ),
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
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> FileUploadResponse:
    """处理单文件上传，并把幂等状态与上传结果保持一致。

    这个接口表面上只是“上传一个文件”，但实际上要串联三件事：
    - 流式接收并做大小限制；
    - 和幂等后端协商，避免重复上传同一请求；
    - 调用 `FileService` 执行真正的校验、落盘和元数据写入。
    """
    svc = FileService(session)
    normalized_content_type = _normalize_upload_content_type(
        file.filename or "unknown",
        file.content_type,
    )
    idempotency_key_header = request.headers.get("Idempotency-Key")
    try:
        idempotency_key = normalize_idempotency_key(idempotency_key_header)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_path = await _spill_upload_to_tempfile(file)
    # 后续所有校验都围绕临时文件进行：
    # - 可以重复读取做 hash / 魔数检查；
    # - 真正失败时也只需要删除临时文件，不会污染正式上传目录。
    idempotency_record_id = None
    idempotency_scope = "file_upload"
    idempotency_key_hash: str | None = None

    async def _safe_mark_processing(payload: dict[str, object]) -> None:
        """尽力把幂等记录标记为处理中；失败只记日志，不阻断主流程。"""
        if idempotency_record_id is None:
            return
        try:
            await idempotency_service.mark_processing(
                record_id=idempotency_record_id,
                response_payload=payload,
            )
            metrics.record_idempotency_event(scope=idempotency_scope, action="processing")
        except Exception:
            logger.warning("Failed to persist idempotency processing state", exc_info=True)

    async def _safe_mark_failed(status_code: int, message: str) -> None:
        """尽力把幂等记录标记为失败。"""
        if idempotency_record_id is None:
            return
        try:
            await idempotency_service.mark_failed(
                record_id=idempotency_record_id,
                response_code=status_code,
                error_message=message,
            )
            metrics.record_idempotency_event(scope=idempotency_scope, action="failed")
        except Exception:
            logger.warning("Failed to persist idempotency failure state", exc_info=True)

    async def _safe_mark_succeeded(payload: dict[str, object]) -> None:
        """尽力把幂等记录标记为成功。"""
        if idempotency_record_id is None:
            return
        try:
            await idempotency_service.mark_succeeded(
                record_id=idempotency_record_id,
                response_code=200,
                response_payload=payload,
            )
            metrics.record_idempotency_event(scope=idempotency_scope, action="succeeded")
        except Exception:
            logger.warning("Failed to persist idempotency success state", exc_info=True)

    try:
        if idempotency_key:
            try:
                # 幂等哈希绑定文件名、内容类型、大小和内容摘要，避免不同文件误复用同一键。
                request_hash = build_request_hash(
                    {
                        "filename": file.filename or "unknown",
                        "content_type": normalized_content_type,
                        "size_bytes": temp_path.stat().st_size,
                        "sha256": storage.compute_sha256_file(temp_path),
                    }
                )
                decision = await idempotency_service.acquire(
                    scope=idempotency_scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                )
                if decision.action == "conflict":
                    # 相同幂等键却对应不同文件内容，通常说明客户端错误复用了 key。
                    metrics.record_idempotency_event(scope=idempotency_scope, action="conflict")
                    raise HTTPException(status_code=409, detail=decision.message or "Idempotency key conflict")
                if decision.action == "in_progress":
                    # 上传流程包含磁盘写入和数据库提交，不能并发重复执行。
                    metrics.record_idempotency_event(scope=idempotency_scope, action="in_progress")
                    raise HTTPException(
                        status_code=409,
                        detail="A request with the same Idempotency-Key is already in progress",
                    )
                if decision.action == "replay":
                    metrics.record_idempotency_event(scope=idempotency_scope, action="replay")
                    payload = decision.response_payload or {}
                    response.headers["X-Idempotency-Replayed"] = "true"
                    # 直接返回之前的成功结果，不再重新走上传/缩略图/落库流程。
                    return FileUploadResponse.model_validate(payload)
                idempotency_record_id = decision.record_id
                idempotency_key_hash = hash_idempotency_key(idempotency_key)
            except HTTPException:
                raise
            except Exception:
                # 幂等后端不可用时，上传功能本身仍可继续，只是失去去重能力。
                logger.warning(
                    "degradation path=/api/files reason=idempotency_backend_unavailable action=upload",
                    exc_info=True,
                )
                metrics.record_degradation(path="/api/files", reason="idempotency_backend_unavailable")
                idempotency_record_id = None
                idempotency_key = None
                idempotency_key_hash = None

        # 只有完成幂等占位后，才真正进入上传流程，避免并发请求重复落盘。
        await _safe_mark_processing({"status": "PROCESSING"})
        record = await svc.upload_from_path(
            filename=file.filename or "unknown",
            content_type=normalized_content_type,
            temp_path=temp_path,
            idempotency_key_hash=idempotency_key_hash,
        )
    except FilePersistenceError as exc:
        await _safe_mark_failed(500, str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except HTTPException as exc:
        if exc.status_code >= 400:
            await _safe_mark_failed(exc.status_code, str(exc.detail))
        raise
    except Exception as exc:
        await _safe_mark_failed(500, str(exc))
        raise
    finally:
        # 无论成功失败都清掉临时文件，避免 `tmp_uploads` 长期堆积。
        temp_path.unlink(missing_ok=True)
    thumb_exists = record.mime_type == "application/pdf" and (Path(record.storage_path).parent / "thumbnail.jpg").exists()
    result = FileUploadResponse(
        id=record.id,
        orig_name=record.orig_name,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        page_count=record.page_count,
        created_at=record.created_at,
        download_url=f"/api/files/{record.id}/download",
        thumbnail_url=f"/api/files/{record.id}/thumbnail" if thumb_exists else None,
    )
    if idempotency_record_id is not None:
        # 只有本次确实创建了幂等记录，才回填成功状态和响应头。
        # replay 场景在上面已经提前返回，这里只处理“本次真的完成了上传”的情况。
        await _safe_mark_succeeded(result.model_dump(mode="json"))
        response.headers["X-Idempotency-Key"] = idempotency_key or ""
    return result


@router.delete(
    "/{file_id}",
    summary="Delete an uploaded file",
)
async def delete_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """删除上传文件，并尽量同时清理数据库记录与磁盘目录。"""
    svc = FileService(session)
    try:
        record = await svc.get(file_id)
    except PDFAgentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    file_dir = _resolve_storage_path(record.storage_path).parent
    persisted = False
    metadata_delete_failed = False
    try:
        result = await session.execute(select(FileRecord).where(FileRecord.id == file_id))
        persisted_record = result.scalar_one_or_none()
        if persisted_record is not None:
            await session.delete(persisted_record)
            await session.commit()
            persisted = True
    except Exception:
        await session.rollback()
        metadata_delete_failed = True
        logger.warning("Failed to delete DB record for %s; removing storage only", file_id, exc_info=True)
    if file_dir.exists():
        # 即使数据库删除失败，也尽量先移除磁盘文件，避免用户看到“删除成功但仍可下载”。
        try:
            shutil.rmtree(file_dir, ignore_errors=False)
        except OSError:
            logger.warning("Failed to remove upload directory for %s", file_id, exc_info=True)
    if file_dir.exists():
        raise HTTPException(status_code=500, detail="Failed to remove file storage")
    if not persisted and load_storage_record(file_id) is not None:
        raise HTTPException(status_code=500, detail="Failed to remove file storage")
    response: dict[str, object] = {"deleted": True, "id": str(file_id)}
    if metadata_delete_failed:
        response["warning"] = "File metadata could not be removed from database"
    return response


@router.get(
    "/{file_id}/download",
    summary="Download an uploaded file",
)
async def download_file(
    file_id: uuid.UUID,
    inline: bool = Query(False, description="Return with inline Content-Disposition for preview"),
    session: AsyncSession = Depends(get_session),
):
    """下载上传文件，支持以内联模式返回给浏览器预览。"""
    svc = FileService(session)
    record = await svc.get(file_id)
    path = _resolve_storage_path(record.storage_path)
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
    """把指定 PDF 页面渲染成 JPG 预览图。"""
    svc = FileService(session)
    record = await svc.get(file_id)

    if record.mime_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files support page preview")

    if record.page_count and (page < 1 or page > record.page_count):
        raise HTTPException(status_code=400, detail=f"Page {page} out of range (1-{record.page_count})")

    pdf_path = _resolve_storage_path(record.storage_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise HTTPException(status_code=503, detail="pdftoppm not installed")

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    render_dir = Path(tempfile.mkdtemp(prefix="page-preview-", dir=settings.data_dir))
    out_stem = render_dir / "page"
    # 渲染过程放在线程里执行外部命令，避免阻塞事件循环。
    result = await asyncio.to_thread(
        run_command,
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
        # 临时渲染目录只服务这一次响应，回完文件后立即清掉。
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
    """返回上传 PDF 的首页缩略图。"""
    svc = FileService(session)
    record = await svc.get(file_id)
    thumb_path = _resolve_storage_path(record.storage_path).parent / "thumbnail.jpg"
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    return FileResponse(thumb_path, media_type="image/jpeg")
