"""Contracts for file service helpers and database fallback behavior."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import io
import uuid
import zipfile

import pytest

from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.db.models import FileRecord
from pdf_agent import services as services_module
from pdf_agent.services import FileService


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _Scalars(self.rows)


class _ScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(
        self,
        result=None,
        *,
        error: Exception | None = None,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ):
        self.result = result
        self.error = error
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.added = []
        self.deleted = []
        self.committed = False
        self.rolled_back = False

    def add(self, record) -> None:
        self.added.append(record)

    async def execute(self, _statement):
        if self.error is not None:
            raise self.error
        return self.result

    async def delete(self, record):
        self.deleted.append(record)

    async def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    async def refresh(self, record):
        return None

    async def rollback(self):
        if self.rollback_error is not None:
            raise self.rollback_error
        self.rolled_back = True


class _Pdf:
    def __init__(self, page_count: int):
        self.pages = [object()] * page_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _configure_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()


def _record(file_id: uuid.UUID | None = None, *, name: str = "sample.pdf", created_at=None) -> FileRecord:
    file_id = file_id or uuid.uuid4()
    created_at = created_at or datetime.now(timezone.utc)
    return FileRecord(
        id=file_id,
        orig_name=name,
        mime_type="application/pdf",
        size_bytes=10,
        sha256="abc",
        idempotency_key_hash=None,
        page_count=1,
        storage_path=f"/tmp/{file_id}/{name}",
        created_at=created_at,
    )


def _zip_bytes(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return buf.getvalue()


def test_validate_magic_bytes_and_declared_content_for_supported_types():
    assert services_module._validate_magic_bytes(b"%PDF-1.4", "application/pdf") is True
    assert services_module._validate_magic_bytes(b"RIFFxxxxWEBPmore", "image/webp") is True
    assert services_module._validate_magic_bytes(b"not-pdf", "application/pdf") is False
    assert services_module._validate_magic_bytes(b"data", "application/unknown") is False
    assert services_module._validate_declared_content(
        _zip_bytes("word/document.xml"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) is True
    assert services_module._validate_declared_content(
        _zip_bytes("xl/workbook.xml"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) is False
    assert services_module._validate_office_container(b"not a zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") is False


def test_validate_declared_content_path_handles_webp_ooxml_and_bad_zip(tmp_path: Path):
    webp = tmp_path / "image.webp"
    webp.write_bytes(b"RIFFxxxxWEBPpayload")
    docx = tmp_path / "doc.docx"
    docx.write_bytes(_zip_bytes("word/document.xml"))
    bad_docx = tmp_path / "bad.docx"
    bad_docx.write_bytes(b"PK\x03\x04broken")
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not-pdf")

    assert services_module._validate_declared_content_path(webp, "image/webp") is True
    assert services_module._validate_declared_content_path(docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True
    assert services_module._validate_declared_content_path(bad_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document") is False
    assert services_module._validate_declared_content_path(bad_pdf, "application/pdf") is False
    assert services_module._validate_office_container(b"%PDF", "application/pdf") is True


def test_generate_thumbnail_handles_missing_binary_failure_success_and_exception(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    thumb_path = tmp_path / "thumb.jpg"
    monkeypatch.setattr(services_module.shutil, "which", lambda _name: None)

    assert services_module._generate_thumbnail(pdf_path, thumb_path) is False

    monkeypatch.setattr(services_module.shutil, "which", lambda _name: "/usr/bin/pdftoppm")
    monkeypatch.setattr(
        services_module,
        "run_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"boom"),
    )
    assert services_module._generate_thumbnail(pdf_path, thumb_path) is False

    def _success(*args, **kwargs):
        thumb_path.with_name("thumb-1.jpg").write_bytes(b"jpg")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(services_module, "run_command", _success)
    assert services_module._generate_thumbnail(pdf_path, thumb_path) is True
    assert thumb_path.read_bytes() == b"jpg"

    existing_thumb = tmp_path / "existing.jpg"
    existing_thumb.write_bytes(b"existing")
    monkeypatch.setattr(
        services_module,
        "run_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""),
    )
    assert services_module._generate_thumbnail(pdf_path, existing_thumb) is True

    missing_thumb = tmp_path / "missing.jpg"
    assert services_module._generate_thumbnail(pdf_path, missing_thumb) is False

    monkeypatch.setattr(services_module, "run_command", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("x")))
    assert services_module._generate_thumbnail(pdf_path, tmp_path / "next.jpg") is False


def test_guess_mime_type_and_page_count_helpers(sample_pdf: Path, tmp_path: Path):
    unknown = tmp_path / "file.unknownext"
    unknown.write_bytes(b"x")
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")

    assert services_module._guess_mime_type(sample_pdf) == "application/pdf"
    assert services_module._guess_mime_type(unknown) == "application/octet-stream"
    assert services_module._load_page_count(sample_pdf, "application/pdf") == 5
    assert services_module._load_page_count(sample_pdf, "image/png") is None
    assert services_module._load_page_count(bad_pdf, "application/pdf") is None


def test_storage_record_loading_uses_filesystem_fallback(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    file_id = uuid.uuid4()
    upload_dir = settings.upload_dir / str(file_id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "thumbnail.jpg").write_bytes(b"skip")
    stored = upload_dir / "a.txt"
    stored.write_text("hello", encoding="utf-8")

    assert services_module._find_storage_file(uuid.uuid4()) is None
    assert services_module._find_storage_file(file_id) == stored
    record = services_module.load_storage_record(file_id)
    assert record is not None
    assert record.id == file_id
    assert record.orig_name == "a.txt"
    assert record.mime_type == "text/plain"


def test_list_storage_records_filters_invalid_dirs_and_sorts(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    older_id = uuid.uuid4()
    newer_id = uuid.uuid4()
    invalid = settings.upload_dir / "not-a-uuid"
    invalid.mkdir()
    (settings.upload_dir / "plain-file").write_text("skip", encoding="utf-8")
    for file_id, name in [(older_id, "older.txt"), (newer_id, "newer.txt")]:
        upload_dir = settings.upload_dir / str(file_id)
        upload_dir.mkdir()
        (upload_dir / name).write_text(name, encoding="utf-8")
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    new_time = datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp()
    (settings.upload_dir / str(older_id) / "older.txt").touch()
    (settings.upload_dir / str(newer_id) / "newer.txt").touch()
    Path(settings.upload_dir / str(older_id) / "older.txt").touch()
    import os

    os.utime(settings.upload_dir / str(older_id) / "older.txt", (old_time, old_time))
    os.utime(settings.upload_dir / str(newer_id) / "newer.txt", (new_time, new_time))

    records = services_module.list_storage_records()

    assert [record.id for record in records] == [newer_id, older_id]


def test_list_storage_records_returns_empty_when_upload_root_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    assert services_module.list_storage_records() == []


@pytest.mark.asyncio
async def test_cleanup_removed_upload_records_deletes_valid_ids_and_ignores_invalid_ids():
    records = [_record(), _record()]
    session = _Session(_RowsResult(records))
    service = FileService(session)

    count = await service._cleanup_removed_upload_records([str(records[0].id), "bad", str(records[1].id)])

    assert count == 2
    assert session.deleted == records
    assert session.committed is True


@pytest.mark.asyncio
async def test_cleanup_removed_upload_records_returns_zero_for_no_valid_ids():
    session = _Session(_RowsResult([]))

    assert await FileService(session)._cleanup_removed_upload_records(["bad"]) == 0
    assert session.committed is False


@pytest.mark.asyncio
async def test_upload_writes_temp_file_and_always_unlinks(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    session = _Session()
    service = FileService(session)
    returned = _record()
    captured: dict[str, Path] = {}

    async def _upload_from_path(*, filename, content_type, temp_path, idempotency_key_hash=None):
        captured["temp_path"] = temp_path
        assert temp_path.exists()
        assert temp_path.read_bytes() == b"content"
        assert filename == "sample.pdf"
        assert content_type == "application/pdf"
        assert idempotency_key_hash is None
        return returned

    monkeypatch.setattr(service, "upload_from_path", _upload_from_path)

    assert await service.upload("sample.pdf", "application/pdf", b"content") is returned
    assert not captured["temp_path"].exists()


@pytest.mark.asyncio
async def test_upload_from_path_rolls_back_when_lru_cleanup_record_sync_fails(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "sample.pdf"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    session = _Session()
    service = FileService(session)
    monkeypatch.setattr(
        services_module.storage,
        "trim_storage_lru_details",
        lambda **_kwargs: SimpleNamespace(removed_upload_ids=["bad-upload"]),
    )
    monkeypatch.setattr(service, "_cleanup_removed_upload_records", lambda _ids: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(PDFAgentError) as exc_info:
        await service.upload_from_path("sample.pdf", "application/pdf", temp_path)

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_FORMAT
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_upload_from_path_rejects_files_larger_than_upload_limit(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "sample.pdf"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)

    with pytest.raises(PDFAgentError) as exc_info:
        await FileService(_Session()).upload_from_path("sample.pdf", "application/pdf", temp_path)

    assert exc_info.value.code == ErrorCode.FILE_TOO_LARGE


@pytest.mark.asyncio
async def test_upload_from_path_rejects_content_type_mismatch(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "sample.pdf"
    temp_path.write_bytes(b"not a pdf")
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)

    with pytest.raises(PDFAgentError) as exc_info:
        await FileService(_Session()).upload_from_path("sample.pdf", "application/pdf", temp_path)

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_FORMAT


@pytest.mark.asyncio
async def test_upload_from_path_rejects_pdf_when_page_count_exceeds_limit(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "sample.pdf"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(settings, "max_page_count", 1)
    monkeypatch.setattr(services_module, "_validate_declared_content_path", lambda *_args: True)
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)
    monkeypatch.setattr(services_module.pikepdf, "open", lambda _path: _Pdf(2))

    with pytest.raises(PDFAgentError) as exc_info:
        await FileService(_Session()).upload_from_path("sample.pdf", "application/pdf", temp_path)

    assert exc_info.value.code == ErrorCode.PAGE_COUNT_EXCEEDED


@pytest.mark.asyncio
async def test_upload_from_path_accepts_encrypted_pdf_and_persists_record(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "encrypted.pdf"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    session = _Session()
    monkeypatch.setattr(services_module, "_validate_declared_content_path", lambda *_args: True)
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)
    monkeypatch.setattr(services_module, "_generate_thumbnail", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        services_module.pikepdf,
        "open",
        lambda _path: (_ for _ in ()).throw(services_module.pikepdf.PasswordError("encrypted")),
    )

    record = await FileService(session).upload_from_path(
        "encrypted.pdf",
        "application/pdf",
        temp_path,
        idempotency_key_hash="key-hash",
    )

    assert record in session.added
    assert session.committed is True
    assert record.page_count is None
    assert record.idempotency_key_hash == "key-hash"
    assert Path(record.storage_path).exists()


@pytest.mark.asyncio
async def test_upload_from_path_rejects_corrupt_pdf(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "corrupt.pdf"
    temp_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(services_module, "_validate_declared_content_path", lambda *_args: True)
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)
    monkeypatch.setattr(services_module.pikepdf, "open", lambda _path: (_ for _ in ()).throw(RuntimeError("bad pdf")))

    with pytest.raises(PDFAgentError) as exc_info:
        await FileService(_Session()).upload_from_path("corrupt.pdf", "application/pdf", temp_path)

    assert exc_info.value.code == ErrorCode.UNSUPPORTED_FORMAT


@pytest.mark.asyncio
async def test_upload_from_path_handles_rollback_and_cleanup_errors_after_commit_failure(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    temp_path = tmp_path / "image.png"
    temp_path.write_bytes(b"\x89PNG\r\n\x1a\npayload")
    session = _Session(commit_error=RuntimeError("commit failed"), rollback_error=RuntimeError("rollback failed"))
    monkeypatch.setattr(services_module.storage, "trim_storage_lru_details", lambda **_kwargs: SimpleNamespace(removed_upload_ids=[]))
    monkeypatch.setattr(services_module.storage, "dir_size_bytes", lambda: 0)
    monkeypatch.setattr(services_module.storage, "storage_limit_bytes", lambda: 10_000)
    monkeypatch.setattr(services_module.shutil, "rmtree", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cleanup failed")))

    with pytest.raises(services_module.FilePersistenceError):
        await FileService(session).upload_from_path("image.png", "image/png", temp_path)

    assert session.added


@pytest.mark.asyncio
async def test_list_count_paginated_and_get_use_database_success_paths():
    records = [_record(), _record()]
    file_id = records[0].id

    assert await FileService(_Session(_RowsResult(records))).list_records() == records
    assert await FileService(_Session(_ScalarOneResult(2))).count_records() == 2
    assert await FileService(_Session(_RowsResult(records))).list_records_paginated(page=2, limit=10) == records
    assert await FileService(_Session(_ScalarOneResult(records[0]))).get(file_id) is records[0]
    assert await FileService(_Session(_ScalarOneResult(records[0]))).get_path(file_id) == Path(records[0].storage_path)


@pytest.mark.asyncio
async def test_list_count_paginated_and_get_fall_back_to_filesystem(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    file_id = uuid.uuid4()
    upload_dir = settings.upload_dir / str(file_id)
    upload_dir.mkdir(parents=True)
    stored = upload_dir / "sample.txt"
    stored.write_text("hello", encoding="utf-8")
    failing = RuntimeError("db down")

    assert [record.id for record in await FileService(_Session(error=failing)).list_records()] == [file_id]
    assert await FileService(_Session(error=failing)).count_records() == 1
    assert [record.id for record in await FileService(_Session(error=failing)).list_records_paginated(page=1, limit=1)] == [file_id]
    assert (await FileService(_Session(error=failing)).get(file_id)).id == file_id


@pytest.mark.asyncio
async def test_get_raises_file_not_found_when_database_and_filesystem_miss(monkeypatch, tmp_path: Path):
    _configure_data_dir(monkeypatch, tmp_path)
    file_id = uuid.uuid4()

    with pytest.raises(PDFAgentError) as exc_info:
        await FileService(_Session(_ScalarOneResult(None))).get(file_id)

    assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
