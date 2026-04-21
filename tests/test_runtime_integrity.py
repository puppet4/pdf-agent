"""Regression coverage for runtime integrity and observability fixes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from pdf_agent.agent import tools_adapter
from pdf_agent.api.files import delete_file
from pdf_agent.api.agent import _load_conversation_messages
from pdf_agent.api.middleware import _load_rate_limit_state
from pdf_agent.api.metrics import _Metrics
from pdf_agent.db.models import FileRecord
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.services import FilePersistenceError, FileService
from pdf_agent.storage import StorageTrimResult
from pdf_agent.tools.base import BaseTool, ToolResult
from pdf_agent.tools._builtins.redact import RedactTool


class _FailingCommitSession:
    def __init__(self) -> None:
        self.added = []
        self.rolled_back = False

    def add(self, record) -> None:
        self.added.append(record)

    async def commit(self) -> None:
        raise RuntimeError("database offline")

    async def refresh(self, record) -> None:  # pragma: no cover - unreachable in this test
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


class _NoopTool(BaseTool):
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="noop",
            label="No-op",
            category="test",
            description="No-op tool for adapter tests",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            async_hint=False,
        )

    def validate(self, params: dict) -> dict:
        return params

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter=None) -> ToolResult:
        if reporter is not None:
            reporter(42, "processing")
        return ToolResult(output_files=[inputs[0]], meta={"ok": True}, log="done")


class _ScalarResult:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _DeleteSession:
    def __init__(self, record):
        self.record = record
        self.deleted = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        return _ScalarResult(self.record)

    async def delete(self, record):
        self.deleted.append(record)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class _FailingDeleteSession(_DeleteSession):
    async def execute(self, _query):
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
async def test_upload_from_path_removes_files_when_metadata_commit_fails(
    tmp_path,
    sample_images,
    monkeypatch: pytest.MonkeyPatch,
):
    from pdf_agent.config import settings
    from pdf_agent import services as services_module

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        services_module.storage,
        "trim_storage_lru_details",
        lambda **kwargs: StorageTrimResult(),
    )

    session = _FailingCommitSession()
    service = FileService(session)

    with pytest.raises(FilePersistenceError, match="persist uploaded file metadata"):
        await service.upload_from_path(
            filename="sample.png",
            content_type="image/png",
            temp_path=sample_images[0],
        )

    assert session.rolled_back is True
    assert not settings.upload_dir.exists() or not any(settings.upload_dir.iterdir())


def test_redact_returns_explicit_warning_when_ghostscript_is_unavailable(
    sample_pdf,
    workdir,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("pdf_agent.tools._builtins.redact.shutil.which", lambda _name: None)

    result = RedactTool().run(
        [sample_pdf],
        {
            "regions_json": '[{"page": 1, "x": 10, "y": 10, "width": 100, "height": 20}]',
            "fill_color": "black",
        },
        workdir,
    )

    assert result.meta["content_removed"] is False
    assert result.meta["redaction_mode"] == "visual_only"
    assert "warning" in result.meta
    assert "visual redaction only" in result.meta["warning"]


def test_async_tool_semaphore_is_safe_to_initialize_inside_worker_threads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tools_adapter, "_ASYNC_SEMAPHORE", None)

    holder: dict[str, object] = {}
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            holder["semaphore"] = tools_adapter._get_semaphore()
        except BaseException as exc:  # pragma: no cover - assertion below exercises this
            errors.append(exc)

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()

    assert errors == []
    assert holder["semaphore"] is tools_adapter._ASYNC_SEMAPHORE


@pytest.mark.asyncio
async def test_execute_tool_uses_run_id_for_progress_and_process_tracking(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    tool = _NoopTool()
    captured: dict[str, str | None] = {"queue_key": None, "bound_run_id": None}
    progress_updates: queue.Queue = queue.Queue()

    def _fake_get_progress_queue(run_id: str) -> queue.Queue:
        captured["queue_key"] = run_id
        return progress_updates

    @contextmanager
    def _fake_bind(run_id: str | None):
        captured["bound_run_id"] = run_id
        yield

    monkeypatch.setattr(tools_adapter, "get_progress_queue", _fake_get_progress_queue)
    monkeypatch.setattr(tools_adapter, "bind_conversation_run_context", _fake_bind)

    state = {
        "files": [
            {
                "file_id": "1",
                "path": str(sample_pdf),
                "orig_name": sample_pdf.name,
                "mime_type": "application/pdf",
                "page_count": 5,
                "source": "upload",
            }
        ],
        "current_files": [str(sample_pdf)],
        "conversation_workdir": str(tmp_path / "conversation"),
        "step_counter": 0,
        "configurable": {"thread_id": "conversation-1", "run_id": "conversation-1:run-a"},
    }

    result = await tools_adapter._execute_tool_with_state(
        tool=tool,
        manifest=tool.manifest(),
        state=state,
        kwargs={},
    )

    assert result.log == "done"
    assert captured["queue_key"] == "conversation-1:run-a"
    assert captured["bound_run_id"] == "conversation-1:run-a"
    update = progress_updates.get_nowait()
    assert update == {"percent": 42, "message": "processing"}


def test_rate_limit_state_fail_opens_when_json_file_is_corrupted(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from pdf_agent.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "rate_limit.json").write_text("{not-json", encoding="utf-8")

    assert _load_rate_limit_state() == {}


@pytest.mark.asyncio
async def test_load_conversation_messages_falls_back_to_local_history_for_state_access_failures(tmp_path: Path):
    conversation_dir = tmp_path / "conversation-1"
    conversation_dir.mkdir(parents=True, exist_ok=True)
    (conversation_dir / ".history.jsonl").write_text(
        '{"type":"human","content":"hello"}\n{"type":"ai","content":"done"}\n',
        encoding="utf-8",
    )

    class _FailingGraph:
        async def aget_state(self, _config):
            raise RuntimeError("checkpoint backend unavailable")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=_FailingGraph())))
    result = await _load_conversation_messages(
        "conversation-1",
        request,
        conversation_dir=conversation_dir,
    )

    assert result.status == "degraded"
    assert result.source == "history"
    assert result.warning is not None
    assert result.messages == [
        {"type": "human", "content": "hello"},
        {"type": "ai", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_load_conversation_messages_does_not_mask_programming_errors():
    class _BadState:
        values = {"messages": [object()]}

    class _BadGraph:
        async def aget_state(self, _config):
            return _BadState()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=_BadGraph())))

    with pytest.raises(AttributeError):
        await _load_conversation_messages(
            "conversation-1",
            request,
            conversation_dir=Path("/tmp/conversation"),
        )


@pytest.mark.asyncio
async def test_delete_file_returns_500_when_storage_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    file_id = uuid.uuid4()
    file_dir = tmp_path / str(file_id)
    file_dir.mkdir(parents=True, exist_ok=True)
    storage_path = file_dir / "sample.pdf"
    storage_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    record = FileRecord(
        id=file_id,
        orig_name="sample.pdf",
        mime_type="application/pdf",
        size_bytes=storage_path.stat().st_size,
        sha256=None,
        page_count=1,
        storage_path=str(storage_path),
        created_at=datetime.now(timezone.utc),
    )
    session = _DeleteSession(record)

    async def _fake_get(self, _: uuid.UUID):
        return record

    def _raise_oserror(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pdf_agent.api.files.FileService.get", _fake_get)
    monkeypatch.setattr("pdf_agent.api.files.shutil.rmtree", _raise_oserror)
    monkeypatch.setattr("pdf_agent.api.files._resolve_storage_path", lambda p: Path(p))

    with pytest.raises(HTTPException) as exc_info:
        await delete_file(file_id=file_id, session=session)

    assert exc_info.value.status_code == 500
    assert "Failed to remove file storage" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_delete_file_returns_warning_when_db_delete_fails_but_storage_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    file_id = uuid.uuid4()
    file_dir = tmp_path / str(file_id)
    file_dir.mkdir(parents=True, exist_ok=True)
    storage_path = file_dir / "sample.pdf"
    storage_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    record = FileRecord(
        id=file_id,
        orig_name="sample.pdf",
        mime_type="application/pdf",
        size_bytes=storage_path.stat().st_size,
        sha256=None,
        page_count=1,
        storage_path=str(storage_path),
        created_at=datetime.now(timezone.utc),
    )
    session = _FailingDeleteSession(record)

    async def _fake_get(self, _: uuid.UUID):
        return record

    monkeypatch.setattr("pdf_agent.api.files.FileService.get", _fake_get)
    monkeypatch.setattr("pdf_agent.api.files._resolve_storage_path", lambda p: Path(p))

    result = await delete_file(file_id=file_id, session=session)

    assert result["deleted"] is True
    assert result["warning"] == "File metadata could not be removed from database"
    assert session.rolled_back is True
    assert not file_dir.exists()


def test_metrics_keep_conversation_duration_separate_from_tool_duration():
    metrics = _Metrics()

    metrics.record_tool("merge", 1.5)
    metrics.record_conversation_run(status="SUCCESS", duration=3.0)
    metrics.record_conversation_state_load(source="history", status="degraded")
    body = metrics.exposition()

    assert 'pdf_agent_tool_duration_seconds_sum{tool="merge"} 1.5000' in body
    assert 'pdf_agent_conversation_run_duration_seconds_sum{status="SUCCESS"} 3.0000' in body
    assert 'pdf_agent_conversation_state_loads_total{source="history",status="degraded"} 1' in body
    assert 'tool="duration:SUCCESS"' not in body


def test_metrics_exposition_skips_malformed_request_counter_keys():
    metrics = _Metrics()
    metrics.request_count["malformed"] = 1

    body = metrics.exposition()

    assert "# HELP pdf_agent_http_requests_total Total HTTP requests" in body
