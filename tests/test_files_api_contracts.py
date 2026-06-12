"""HTTP contract tests for uploaded file API behavior."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from pdf_agent.api import files as files_api
from pdf_agent.api.files import router as files_router
from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError, error_http_status, localized_error
from pdf_agent.db import get_session
from pdf_agent.db.models import FileRecord
from pdf_agent.services import FilePersistenceError
from pdf_agent.services.idempotency import IdempotencyDecision


class _NoopSession:
    pass


@pytest.fixture()
def files_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    settings.ensure_dirs()

    app = FastAPI()

    @app.exception_handler(PDFAgentError)
    async def _pdf_agent_error_handler(request: Request, exc: PDFAgentError) -> JSONResponse:
        locale = "zh" if "zh" in request.headers.get("Accept-Language", "") else settings.default_locale
        return JSONResponse(
            status_code=error_http_status(exc.code),
            content={"error_code": exc.code, "message": localized_error(exc.code, exc.message, locale)},
        )

    async def _session_override():
        yield _NoopSession()

    app.dependency_overrides[get_session] = _session_override
    app.include_router(files_router)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _record(
    file_id: uuid.UUID,
    path: Path,
    *,
    orig_name: str = "sample.pdf",
    mime_type: str = "application/pdf",
    page_count: int | None = 1,
) -> FileRecord:
    return FileRecord(
        id=file_id,
        orig_name=orig_name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size if path.exists() else 0,
        sha256=None,
        idempotency_key_hash=None,
        page_count=page_count,
        storage_path=str(path),
        created_at=datetime.now(timezone.utc),
    )


def test_upload_recovers_pdf_mime_type_from_filename_when_browser_sends_octet_stream(
    files_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    file_id = uuid.uuid4()
    stored_path = settings.upload_dir / str(file_id) / "report.pdf"
    captured: dict[str, object] = {}

    class _UploadService:
        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(
            self,
            *,
            filename: str,
            content_type: str,
            temp_path: Path,
            idempotency_key_hash: str | None = None,
        ) -> FileRecord:
            captured["filename"] = filename
            captured["content_type"] = content_type
            captured["temp_path"] = temp_path
            captured["temp_exists_during_call"] = temp_path.exists()
            captured["idempotency_key_hash"] = idempotency_key_hash
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            stored_path.write_bytes(temp_path.read_bytes())
            return _record(file_id, stored_path, orig_name=filename, mime_type=content_type)

    monkeypatch.setattr("pdf_agent.api.files.FileService", _UploadService)

    response = files_client.post(
        "/api/files",
        files={"file": ("report.pdf", b"%PDF-1.4\n%%EOF\n", "application/octet-stream")},
    )

    assert response.status_code == 200
    assert response.json()["mime_type"] == "application/pdf"
    assert response.json()["download_url"] == f"/api/files/{file_id}/download"
    assert captured["filename"] == "report.pdf"
    assert captured["content_type"] == "application/pdf"
    assert captured["temp_exists_during_call"] is True
    assert captured["idempotency_key_hash"] is None
    assert not Path(captured["temp_path"]).exists()


def test_upload_rejects_oversized_body_before_file_persistence(
    files_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    class _UnexpectedService:
        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(self, **_kwargs) -> FileRecord:
            nonlocal called
            called = True
            raise AssertionError("upload_from_path should not be called after size validation fails")

    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    monkeypatch.setattr("pdf_agent.api.files.FileService", _UnexpectedService)

    response = files_client.post(
        "/api/files",
        files={"file": ("too-large.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["error_code"] == ErrorCode.FILE_TOO_LARGE
    assert called is False
    assert not any((settings.data_dir / "tmp_uploads").glob("*"))


def test_download_rejects_storage_paths_outside_upload_root(
    files_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    file_id = uuid.uuid4()
    outside_path = tmp_path / "outside.pdf"
    outside_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    class _DownloadService:
        def __init__(self, _session) -> None:
            pass

        async def get(self, requested_id: uuid.UUID) -> FileRecord:
            assert requested_id == file_id
            return _record(file_id, outside_path)

    monkeypatch.setattr("pdf_agent.api.files.FileService", _DownloadService)

    response = files_client.get(f"/api/files/{file_id}/download")

    assert response.status_code == 500
    assert response.json()["detail"] == "Storage path validation failed"


def test_page_preview_rejects_non_pdf_before_renderer_lookup(
    files_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    file_id = uuid.uuid4()
    image_path = settings.upload_dir / str(file_id) / "sample.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    class _ImageService:
        def __init__(self, _session) -> None:
            pass

        async def get(self, requested_id: uuid.UUID) -> FileRecord:
            assert requested_id == file_id
            return _record(file_id, image_path, orig_name="sample.png", mime_type="image/png", page_count=None)

    def _unexpected_renderer_lookup(_name: str) -> str | None:
        raise AssertionError("renderer lookup should not happen for non-PDF files")

    monkeypatch.setattr("pdf_agent.api.files.FileService", _ImageService)
    monkeypatch.setattr("pdf_agent.api.files.shutil.which", _unexpected_renderer_lookup)

    response = files_client.get(f"/api/files/{file_id}/pages/1")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only PDF files support page preview"


def test_thumbnail_returns_404_when_pdf_thumbnail_was_not_generated(
    files_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    file_id = uuid.uuid4()
    pdf_path = settings.upload_dir / str(file_id) / "sample.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    class _PdfService:
        def __init__(self, _session) -> None:
            pass

        async def get(self, requested_id: uuid.UUID) -> FileRecord:
            assert requested_id == file_id
            return _record(file_id, pdf_path)

    monkeypatch.setattr("pdf_agent.api.files.FileService", _PdfService)

    response = files_client.get(f"/api/files/{file_id}/thumbnail")

    assert response.status_code == 404
    assert response.json()["detail"] == "Thumbnail not available"


@pytest.mark.asyncio
async def test_list_files_impl_paginates_and_exposes_thumbnail_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    file_id = uuid.uuid4()
    stored_path = settings.upload_dir / str(file_id) / "sample.pdf"
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    (stored_path.parent / "thumbnail.jpg").write_bytes(b"jpg")
    record = _record(file_id, stored_path)

    class _ListingService:
        def __init__(self, session) -> None:
            assert session == "session"

        async def count_records(self) -> int:
            return 1

        async def list_records_paginated(self, page: int, limit: int) -> list[FileRecord]:
            assert (page, limit) == (1, 200)
            return [record]

    monkeypatch.setattr(files_api, "FileService", _ListingService)

    payload = await files_api._list_files_impl(page=0, limit=999, session="session")

    assert payload["total"] == 1
    assert payload["limit"] == 200
    assert payload["files"][0]["thumbnail_url"] == f"/api/files/{file_id}/thumbnail"

    wrapper_payload = await files_api.list_files(page=1, limit=200, session="session")
    assert wrapper_payload["files"][0]["id"] == str(file_id)


class _FakeIdempotencyService:
    def __init__(self, decision: IdempotencyDecision | Exception) -> None:
        self.decision = decision
        self.processing: list[dict[str, object]] = []
        self.failed: list[tuple[int, str]] = []
        self.succeeded: list[dict[str, object]] = []

    async def acquire(self, **_kwargs):
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision

    async def mark_processing(self, *, record_id, response_payload):
        self.processing.append(response_payload)

    async def mark_failed(self, *, record_id, response_code, error_message):
        self.failed.append((response_code, error_message))

    async def mark_succeeded(self, *, record_id, response_code, response_payload):
        assert response_code == 200
        self.succeeded.append(response_payload)


class _FailingMarkIdempotencyService(_FakeIdempotencyService):
    async def mark_processing(self, *, record_id, response_payload):
        raise RuntimeError("processing state down")

    async def mark_failed(self, *, record_id, response_code, error_message):
        raise RuntimeError("failed state down")

    async def mark_succeeded(self, *, record_id, response_code, response_payload):
        raise RuntimeError("success state down")


@pytest.mark.asyncio
async def test_upload_file_idempotency_success_replay_conflict_and_degraded_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    temp_path = tmp_path / "upload.tmp"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    file_id = uuid.uuid4()
    stored_path = settings.upload_dir / str(file_id) / "sample.pdf"

    async def fake_spill(_file):
        temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return temp_path

    class _UploadService:
        calls: list[dict[str, object]] = []

        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(self, **kwargs) -> FileRecord:
            self.calls.append(kwargs)
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            stored_path.write_bytes(Path(kwargs["temp_path"]).read_bytes())
            return _record(file_id, stored_path, orig_name=kwargs["filename"], mime_type=kwargs["content_type"])

    monkeypatch.setattr(files_api, "_spill_upload_to_tempfile", fake_spill)
    monkeypatch.setattr(files_api, "FileService", _UploadService)
    monkeypatch.setattr(files_api.storage, "compute_sha256_file", lambda _path: "sha256")

    fake_idem = _FakeIdempotencyService(IdempotencyDecision(action="acquired", record_id=uuid.uuid4()))
    monkeypatch.setattr(files_api, "idempotency_service", fake_idem)
    response = Response()
    result = await files_api.upload_file(
        file=SimpleNamespace(filename="sample.pdf", content_type="application/octet-stream"),
        request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
        response=response,
        session=_NoopSession(),
    )

    assert result.id == file_id
    assert response.headers["X-Idempotency-Key"] == "upload-key"
    assert fake_idem.processing == [{"status": "PROCESSING"}]
    assert fake_idem.succeeded[0]["id"] == str(file_id)
    assert _UploadService.calls[-1]["idempotency_key_hash"] is not None
    assert not temp_path.exists()

    replay_payload = result.model_dump(mode="json")
    replay_idem = _FakeIdempotencyService(IdempotencyDecision(action="replay", response_payload=replay_payload))
    monkeypatch.setattr(files_api, "idempotency_service", replay_idem)
    replay_response = Response()
    replayed = await files_api.upload_file(
        file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
        request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
        response=replay_response,
        session=_NoopSession(),
    )
    assert replayed.id == file_id
    assert replay_response.headers["X-Idempotency-Replayed"] == "true"

    monkeypatch.setattr(
        files_api,
        "idempotency_service",
        _FakeIdempotencyService(IdempotencyDecision(action="conflict", message="mismatch")),
    )
    with pytest.raises(HTTPException) as conflict:
        await files_api.upload_file(
            file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
            request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
            response=Response(),
            session=_NoopSession(),
        )
    assert conflict.value.status_code == 409

    monkeypatch.setattr(
        files_api,
        "idempotency_service",
        _FakeIdempotencyService(RuntimeError("idempotency unavailable")),
    )
    degraded = await files_api.upload_file(
        file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
        request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
        response=Response(),
        session=_NoopSession(),
    )
    assert degraded.id == file_id
    assert _UploadService.calls[-1]["idempotency_key_hash"] is None


@pytest.mark.asyncio
async def test_upload_file_marks_failures_for_persistence_and_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    temp_path = tmp_path / "upload.tmp"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    async def fake_spill(_file):
        temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return temp_path

    class _FailingService:
        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(self, **_kwargs) -> FileRecord:
            raise FilePersistenceError("disk full")

    fake_idem = _FakeIdempotencyService(IdempotencyDecision(action="acquired", record_id=uuid.uuid4()))
    monkeypatch.setattr(files_api, "_spill_upload_to_tempfile", fake_spill)
    monkeypatch.setattr(files_api, "FileService", _FailingService)
    monkeypatch.setattr(files_api, "idempotency_service", fake_idem)
    monkeypatch.setattr(files_api.storage, "compute_sha256_file", lambda _path: "sha256")

    with pytest.raises(HTTPException) as persistence:
        await files_api.upload_file(
            file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
            request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
            response=Response(),
            session=_NoopSession(),
        )
    assert persistence.value.status_code == 500
    assert fake_idem.failed[-1] == (500, "disk full")
    assert not temp_path.exists()

    monkeypatch.setattr(
        files_api,
        "idempotency_service",
        _FakeIdempotencyService(IdempotencyDecision(action="in_progress", response_payload={"status": "PROCESSING"})),
    )
    with pytest.raises(HTTPException) as in_progress:
        await files_api.upload_file(
            file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
            request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
            response=Response(),
            session=_NoopSession(),
        )
    assert in_progress.value.status_code == 409


@pytest.mark.asyncio
async def test_upload_file_invalid_key_and_safe_mark_degradation_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    temp_path = tmp_path / "upload.tmp"
    file_id = uuid.uuid4()
    stored_path = settings.upload_dir / str(file_id) / "sample.pdf"

    async def fake_spill(_file):
        temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return temp_path

    class _UploadService:
        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(self, **kwargs) -> FileRecord:
            stored_path.parent.mkdir(parents=True, exist_ok=True)
            stored_path.write_bytes(Path(kwargs["temp_path"]).read_bytes())
            return _record(file_id, stored_path, orig_name=kwargs["filename"], mime_type=kwargs["content_type"])

    monkeypatch.setattr(files_api, "_spill_upload_to_tempfile", fake_spill)
    monkeypatch.setattr(files_api, "FileService", _UploadService)
    monkeypatch.setattr(files_api.storage, "compute_sha256_file", lambda _path: "sha256")

    with pytest.raises(HTTPException) as invalid_key:
        await files_api.upload_file(
            file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
            request=SimpleNamespace(headers={"Idempotency-Key": "x" * (settings.idempotency_max_key_length + 1)}),
            response=Response(),
            session=_NoopSession(),
        )
    assert invalid_key.value.status_code == 400

    failing_idem = _FailingMarkIdempotencyService(IdempotencyDecision(action="acquired", record_id=uuid.uuid4()))
    monkeypatch.setattr(files_api, "idempotency_service", failing_idem)
    response = Response()
    result = await files_api.upload_file(
        file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
        request=SimpleNamespace(headers={"Idempotency-Key": "upload-key"}),
        response=response,
        session=_NoopSession(),
    )
    assert result.id == file_id
    assert response.headers["X-Idempotency-Key"] == "upload-key"

    class _ExplodingService:
        def __init__(self, _session) -> None:
            pass

        async def upload_from_path(self, **_kwargs) -> FileRecord:
            raise RuntimeError("unexpected upload failure")

    monkeypatch.setattr(files_api, "FileService", _ExplodingService)
    with pytest.raises(RuntimeError, match="unexpected upload failure"):
        await files_api.upload_file(
            file=SimpleNamespace(filename="sample.pdf", content_type="application/pdf"),
            request=SimpleNamespace(headers={"Idempotency-Key": "upload-key-2"}),
            response=Response(),
            session=_NoopSession(),
        )


@pytest.mark.asyncio
async def test_delete_download_page_preview_and_thumbnail_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    file_id = uuid.uuid4()
    stored_path = settings.upload_dir / str(file_id) / "sample.pdf"
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    (stored_path.parent / "thumbnail.jpg").write_bytes(b"jpg")
    record = _record(file_id, stored_path, page_count=2)

    class _Service:
        def __init__(self, _session) -> None:
            pass

        async def get(self, requested_id: uuid.UUID) -> FileRecord:
            if requested_id != file_id:
                raise PDFAgentError(ErrorCode.FILE_NOT_FOUND, "missing")
            return record

    class _Scalar:
        def scalar_one_or_none(self):
            return record

    class _DeleteSession:
        deleted = False
        committed = False
        rolled_back = False

        async def execute(self, _query):
            return _Scalar()

        async def delete(self, _record):
            self.deleted = True

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    monkeypatch.setattr(files_api, "FileService", _Service)
    download = await files_api.download_file(file_id, inline=True, session=_NoopSession())
    assert Path(download.path) == stored_path
    assert "inline" in download.headers["content-disposition"]

    missing_path = stored_path.with_name("missing.pdf")
    record.storage_path = str(missing_path)
    with pytest.raises(HTTPException) as missing_disk:
        await files_api.download_file(file_id, inline=False, session=_NoopSession())
    assert missing_disk.value.status_code == 404
    record.storage_path = str(stored_path)

    with pytest.raises(HTTPException) as page_range:
        await files_api.get_page_image(file_id, 3, session=_NoopSession())
    assert page_range.value.status_code == 400

    missing_path = stored_path.with_name("page-missing.pdf")
    record.storage_path = str(missing_path)
    with pytest.raises(HTTPException) as page_missing_disk:
        await files_api.get_page_image(file_id, 1, session=_NoopSession())
    assert page_missing_disk.value.status_code == 404
    record.storage_path = str(stored_path)

    monkeypatch.setattr(files_api.shutil, "which", lambda name: None)
    with pytest.raises(HTTPException) as no_renderer:
        await files_api.get_page_image(file_id, 1, session=_NoopSession())
    assert no_renderer.value.status_code == 503

    monkeypatch.setattr(files_api.shutil, "which", lambda name: "/usr/bin/pdftoppm")

    def fake_run_command(cmd, **_kwargs):
        Path(str(cmd[-1]) + "-1.jpg").write_bytes(b"jpg")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(files_api, "run_command", fake_run_command)
    preview = await files_api.get_page_image(file_id, 1, session=_NoopSession())
    assert preview.media_type == "image/jpeg"

    def failing_run_command(_cmd, **_kwargs):
        return SimpleNamespace(returncode=1, stderr=b"bad pdf")

    monkeypatch.setattr(files_api, "run_command", failing_run_command)
    with pytest.raises(HTTPException) as render_failed:
        await files_api.get_page_image(file_id, 1, session=_NoopSession())
    assert render_failed.value.status_code == 500
    assert "bad pdf" in render_failed.value.detail

    monkeypatch.setattr(files_api, "run_command", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=b""))
    with pytest.raises(HTTPException) as no_rendered_image:
        await files_api.get_page_image(file_id, 1, session=_NoopSession())
    assert no_rendered_image.value.status_code == 500
    assert no_rendered_image.value.detail == "Failed to render page"

    thumb = await files_api.get_thumbnail(file_id, session=_NoopSession())
    assert Path(thumb.path) == stored_path.parent / "thumbnail.jpg"

    with pytest.raises(HTTPException) as missing_service:
        await files_api.delete_file(uuid.uuid4(), session=_DeleteSession())
    assert missing_service.value.status_code == 404

    delete_session = _DeleteSession()
    deleted = await files_api.delete_file(file_id, session=delete_session)
    assert deleted == {"deleted": True, "id": str(file_id)}
    assert delete_session.deleted is True
    assert delete_session.committed is True

    class _NoPersistScalar:
        def scalar_one_or_none(self):
            return None

    class _NoPersistSession(_DeleteSession):
        async def execute(self, _query):
            return _NoPersistScalar()

    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(files_api, "load_storage_record", lambda _file_id: {"id": str(file_id)})
    with pytest.raises(HTTPException) as storage_record_left:
        await files_api.delete_file(file_id, session=_NoPersistSession())
    assert storage_record_left.value.status_code == 500
