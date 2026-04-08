"""Regression coverage for runtime integrity and observability fixes."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pdf_agent.agent import tools_adapter
from pdf_agent.api.agent import _load_conversation_messages
from pdf_agent.api.middleware import _load_rate_limit_state
from pdf_agent.api.metrics import _Metrics
from pdf_agent.services import FilePersistenceError, FileService
from pdf_agent.storage import StorageTrimResult
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


def test_rate_limit_state_fail_opens_when_json_file_is_corrupted(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from pdf_agent.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "rate_limit.json").write_text("{not-json", encoding="utf-8")

    assert _load_rate_limit_state() == {}


@pytest.mark.asyncio
async def test_load_conversation_messages_returns_503_for_state_access_failures():
    class _FailingGraph:
        async def aget_state(self, _config):
            raise RuntimeError("checkpoint backend unavailable")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=_FailingGraph())))

    with pytest.raises(HTTPException) as exc_info:
        await _load_conversation_messages("conversation-1", request)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_load_conversation_messages_does_not_mask_programming_errors():
    class _BadState:
        values = {"messages": [object()]}

    class _BadGraph:
        async def aget_state(self, _config):
            return _BadState()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=_BadGraph())))

    with pytest.raises(AttributeError):
        await _load_conversation_messages("conversation-1", request)


def test_metrics_keep_conversation_duration_separate_from_tool_duration():
    metrics = _Metrics()

    metrics.record_tool("merge", 1.5)
    metrics.record_conversation_run(status="SUCCESS", duration=3.0)
    body = metrics.exposition()

    assert 'pdf_agent_tool_duration_seconds_sum{tool="merge"} 1.5000' in body
    assert 'pdf_agent_conversation_run_duration_seconds_sum{status="SUCCESS"} 3.0000' in body
    assert 'tool="duration:SUCCESS"' not in body


def test_metrics_exposition_skips_malformed_request_counter_keys():
    metrics = _Metrics()
    metrics.request_count["malformed"] = 1

    body = metrics.exposition()

    assert "# HELP pdf_agent_http_requests_total Total HTTP requests" in body
