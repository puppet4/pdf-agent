"""Contracts for local storage behavior."""
from __future__ import annotations

import os
from pathlib import Path
import time
import uuid

from pdf_agent.config import settings
from pdf_agent.storage import LocalStorage, StorageTrimResult


def _configure_storage_root(monkeypatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "conversation_ttl_hours", 1)
    monkeypatch.setattr(settings, "storage_scan_cache_ttl_sec", 60)
    settings.ensure_dirs()
    return data_dir


def test_storage_trim_result_counts_removed_items():
    result = StorageTrimResult(removed_conversation_ids=["c1"], removed_upload_ids=["u1", "u2"])

    assert result.total_removed == 3


def test_save_upload_sanitizes_filename_writes_file_and_invalidates_cache(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    storage._size_cache["stale"] = (time.time(), (True, 1, 1), 123)  # noqa: SLF001
    file_id = uuid.uuid4()

    path = storage.save_upload(file_id, "../report.pdf", b"pdf-data")

    assert path == settings.upload_dir / str(file_id) / "report.pdf"
    assert path.read_bytes() == b"pdf-data"
    assert not path.with_suffix(".pdf.tmp").exists()
    assert storage._size_cache == {}  # noqa: SLF001


def test_save_upload_removes_temp_file_when_atomic_write_fails(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    file_id = uuid.uuid4()
    original_replace = os.replace

    def _fail_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _fail_replace)

    try:
        try:
            storage.save_upload(file_id, "report.pdf", b"pdf-data")
        except OSError as exc:
            assert "disk full" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("save_upload should have raised")
    finally:
        monkeypatch.setattr(os, "replace", original_replace)

    assert not (settings.upload_dir / str(file_id) / "report.pdf.tmp").exists()
    assert not (settings.upload_dir / str(file_id) / "report.pdf").exists()


def test_save_upload_from_path_and_path_helpers(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = uuid.uuid4()

    path = storage.save_upload_from_path(file_id, "../../input.txt", source)

    assert path == storage.get_upload_path(file_id, "input.txt")
    assert path.read_text(encoding="utf-8") == "hello"
    assert storage.compute_sha256(b"hello") == storage.compute_sha256_file(path)


def test_save_upload_from_path_removes_temp_file_when_copy_succeeds_but_replace_fails(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    file_id = uuid.uuid4()

    def _fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", _fail_replace)

    try:
        storage.save_upload_from_path(file_id, "input.txt", source)
    except OSError as exc:
        assert "replace failed" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("save_upload_from_path should have raised")

    assert not (settings.upload_dir / str(file_id) / "input.txt.tmp").exists()
    assert not (settings.upload_dir / str(file_id) / "input.txt").exists()


def test_conversation_workdir_and_step_dir_are_created(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()

    workdir = storage.create_conversation_workdir("conv-1")
    step_dir = storage.create_conversation_step_dir("conv-1", 2)

    assert workdir == settings.conversations_dir / "conv-1"
    assert workdir.is_dir()
    assert step_dir == workdir / "step_2"
    assert step_dir.is_dir()


def test_cleanup_conversation_removes_existing_dir_and_invalidates_cache(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    workdir = storage.create_conversation_workdir("conv-1")
    (workdir / "artifact.pdf").write_bytes(b"x")
    storage._size_cache["stale"] = (time.time(), (True, 1, 1), 123)  # noqa: SLF001

    storage.cleanup_conversation("conv-1")
    storage.cleanup_conversation("missing")

    assert not workdir.exists()
    assert storage._size_cache == {}  # noqa: SLF001


def test_list_and_cleanup_expired_conversations(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    old = storage.create_conversation_workdir("old")
    fresh = storage.create_conversation_workdir("fresh")
    (settings.conversations_dir / "not-a-dir").write_text("skip", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 7200, now - 7200))
    os.utime(fresh, (now, now))

    assert storage.list_expired_conversations() == ["old"]
    assert storage.cleanup_expired_conversations() == 1
    assert not old.exists()
    assert fresh.exists()


def test_list_expired_conversations_ignores_entries_that_cannot_be_statted(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    bad = storage.create_conversation_workdir("bad")
    original_stat = Path.stat
    original_is_dir = Path.is_dir

    def _stat(path: Path, *args, **kwargs):
        if path == bad:
            raise OSError("cannot stat")
        return original_stat(path, *args, **kwargs)

    def _is_dir(path: Path):
        if path == bad:
            return True
        return original_is_dir(path)

    monkeypatch.setattr(Path, "stat", _stat)
    monkeypatch.setattr(Path, "is_dir", _is_dir)

    assert storage.list_expired_conversations() == []


def test_cleanup_expired_conversations_ignores_cleanup_errors(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    monkeypatch.setattr(storage, "list_expired_conversations", lambda: ["bad"])
    monkeypatch.setattr(storage, "cleanup_conversation", lambda _conversation_id: (_ for _ in ()).throw(OSError("nope")))

    assert storage.cleanup_expired_conversations() == 0


def test_list_expired_conversations_returns_empty_when_root_is_absent(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)

    assert LocalStorage().list_expired_conversations() == []


def test_cleanup_expired_uploads_removes_old_upload_dirs(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    old = settings.upload_dir / "old-upload"
    fresh = settings.upload_dir / "fresh-upload"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (settings.upload_dir / "plain-file").write_text("skip", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 7200, now - 7200))
    os.utime(fresh, (now, now))
    storage._size_cache["stale"] = (now, (True, 1, 1), 123)  # noqa: SLF001

    assert storage.cleanup_expired_uploads() == ["old-upload"]
    assert not old.exists()
    assert fresh.exists()
    assert storage._size_cache == {}  # noqa: SLF001


def test_cleanup_expired_uploads_ignores_cleanup_errors(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    bad = settings.upload_dir / "bad-upload"
    bad.mkdir(parents=True)
    now = time.time()
    os.utime(bad, (now - 7200, now - 7200))

    def _fail_rmtree(path: Path):
        if path == bad:
            raise OSError("cannot delete")

    monkeypatch.setattr("pdf_agent.storage.shutil.rmtree", _fail_rmtree)

    assert storage.cleanup_expired_uploads() == []
    assert bad.exists()


def test_cleanup_expired_uploads_returns_empty_when_root_is_absent(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    assert LocalStorage().cleanup_expired_uploads() == []


def test_dir_size_cache_uses_root_signature_and_force_refresh(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    root = tmp_path / "cache-root"
    root.mkdir()
    (root / "a.txt").write_bytes(b"1234")
    scans = 0

    def _scan(path: Path) -> int:
        nonlocal scans
        scans += 1
        return 4

    monkeypatch.setattr(storage, "_scan_dir_size_bytes", _scan)

    assert storage.dir_size_bytes(root) == 4
    assert storage.dir_size_bytes(root) == 4
    assert scans == 1
    assert storage.dir_size_bytes(root, force_refresh=True) == 4
    assert scans == 2
    storage.invalidate_size_cache(root)
    assert storage.dir_size_bytes(root) == 4
    assert scans == 3


def test_root_signature_and_scan_dir_size_handle_missing_paths(tmp_path: Path):
    storage = LocalStorage()
    missing = tmp_path / "missing"

    assert storage._root_signature(missing) == (False, 0, 0)  # noqa: SLF001
    assert storage._scan_dir_size_bytes(missing) == 0  # noqa: SLF001


def test_root_signature_returns_missing_signature_when_stat_fails(monkeypatch, tmp_path: Path):
    storage = LocalStorage()
    root = tmp_path / "root"
    root.mkdir()
    original_exists = Path.exists
    original_stat = Path.stat

    def _exists(path: Path):
        if path == root:
            return True
        return original_exists(path)

    def _stat(path: Path, *args, **kwargs):
        if path == root:
            raise OSError("cannot stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", _exists)
    monkeypatch.setattr(Path, "stat", _stat)

    assert storage._root_signature(root) == (False, 0, 0)  # noqa: SLF001


def test_scan_dir_size_ignores_directories_and_sums_files(tmp_path: Path):
    storage = LocalStorage()
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "a.bin").write_bytes(b"123")
    (nested / "b.bin").write_bytes(b"4567")

    assert storage._scan_dir_size_bytes(root) == 7  # noqa: SLF001


def test_scan_dir_size_ignores_files_that_cannot_be_statted(monkeypatch, tmp_path: Path):
    storage = LocalStorage()
    root = tmp_path / "root"
    root.mkdir()
    bad = root / "bad.bin"
    good = root / "good.bin"
    bad.write_bytes(b"123")
    good.write_bytes(b"4567")
    original_stat = Path.stat
    original_is_file = Path.is_file

    def _stat(path: Path, *args, **kwargs):
        if path == bad:
            raise OSError("cannot stat")
        return original_stat(path, *args, **kwargs)

    def _is_file(path: Path):
        if path == bad:
            return True
        return original_is_file(path)

    monkeypatch.setattr(Path, "stat", _stat)
    monkeypatch.setattr(Path, "is_file", _is_file)

    assert storage._scan_dir_size_bytes(root) == 4  # noqa: SLF001


def test_storage_limit_bytes_uses_configured_gigabytes(monkeypatch):
    monkeypatch.setattr(settings, "max_storage_gb", 2)

    assert LocalStorage().storage_limit_bytes() == 2 * 1024 * 1024 * 1024


def test_trim_storage_lru_details_removes_oldest_dirs_until_under_limit(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 5)
    old_conv = settings.conversations_dir / "old-conv"
    new_upload = settings.upload_dir / "new-upload"
    old_conv.mkdir(parents=True)
    new_upload.mkdir(parents=True)
    (old_conv / "a.bin").write_bytes(b"123456")
    (new_upload / "b.bin").write_bytes(b"1234")
    now = time.time()
    os.utime(old_conv, (now - 20, now - 20))
    os.utime(new_upload, (now - 10, now - 10))

    result = storage.trim_storage_lru_details()

    assert result.removed_conversation_ids == ["old-conv"]
    assert result.removed_upload_ids == []
    assert not old_conv.exists()
    assert new_upload.exists()


def test_trim_storage_lru_details_skips_missing_roots_and_unstattable_candidates(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 0)
    monkeypatch.setattr(storage, "dir_size_bytes", lambda *, force_refresh=False: 3)
    shutil_upload = settings.upload_dir / "bad-upload"
    shutil_upload.mkdir(parents=True)
    (shutil_upload / "a.bin").write_bytes(b"123")
    settings.conversations_dir.rmdir()
    original_stat = Path.stat
    original_is_dir = Path.is_dir

    def _stat(path: Path, *args, **kwargs):
        if path == shutil_upload:
            raise OSError("cannot stat")
        return original_stat(path, *args, **kwargs)

    def _is_dir(path: Path):
        if path == shutil_upload:
            return True
        return original_is_dir(path)

    monkeypatch.setattr(Path, "stat", _stat)
    monkeypatch.setattr(Path, "is_dir", _is_dir)

    result = storage.trim_storage_lru_details()

    assert result.total_removed == 0
    assert original_stat(shutil_upload).st_mode


def test_trim_storage_lru_details_respects_include_flags(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 0)
    conv = settings.conversations_dir / "conv"
    upload = settings.upload_dir / "upload"
    conv.mkdir(parents=True)
    upload.mkdir(parents=True)
    (conv / "a.bin").write_bytes(b"123")
    (upload / "b.bin").write_bytes(b"456")

    result = storage.trim_storage_lru_details(include_conversations=False, include_uploads=True)

    assert result.removed_conversation_ids == []
    assert result.removed_upload_ids == ["upload"]
    assert conv.exists()
    assert not upload.exists()


def test_trim_storage_lru_and_under_limit_paths(monkeypatch, tmp_path: Path):
    _configure_storage_root(monkeypatch, tmp_path)
    storage = LocalStorage()
    monkeypatch.setattr(storage, "storage_limit_bytes", lambda: 10_000)

    assert storage.trim_storage_lru_details().total_removed == 0
    assert storage.trim_storage_lru() == 0
