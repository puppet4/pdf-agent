"""Focused contracts for core reducers, page ranges, middleware, and metrics."""
from __future__ import annotations

from types import SimpleNamespace

from starlette.responses import Response
import pytest

from pdf_agent import i18n
from pdf_agent import db as db_module
from pdf_agent.agent.state import files_reducer
from pdf_agent.api import metrics as metrics_module
from pdf_agent.api import middleware
from pdf_agent.config import settings
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.core.page_range import parse_page_range
from pdf_agent.schemas.tool import ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ToolResult
from pdf_agent.tools.registry import ToolRegistry


def test_files_reducer_deduplicates_by_path():
    existing = [{"path": "/tmp/a.pdf", "file_id": "1"}]
    new = [{"path": "/tmp/a.pdf", "file_id": "dup"}, {"path": "/tmp/b.pdf", "file_id": "2"}]

    assert files_reducer(existing, new) == [
        {"path": "/tmp/a.pdf", "file_id": "1"},
        {"path": "/tmp/b.pdf", "file_id": "2"},
    ]


def test_page_range_parser_success_and_error_edges():
    assert parse_page_range("odd", 5) == [0, 2, 4]
    assert parse_page_range("even", 5) == [1, 3]
    assert parse_page_range("last-2-last,2", 5) == [2, 3, 4, 1]
    assert parse_page_range("1,1,2", 5) == [0, 1]

    for expr in ("", "bad", "0", "6", "3-2"):
        with pytest.raises(PDFAgentError) as exc_info:
            parse_page_range(expr, 5)
        assert exc_info.value.code == ErrorCode.INVALID_PAGE_RANGE


def test_metrics_records_escapes_normalizes_and_skips_malformed_keys():
    store = metrics_module._Metrics()
    store.record_request("GET", '/api/files/"x"', 200, 0.25)
    store.record_tool("merge", 1.5)
    store.record_conversation_run(status="SUCCESS", duration=2.0)
    store.record_conversation_run(status="CANCELLED", duration=None)
    store.record_conversation_state_load(source="history", status="degraded")
    store.record_degradation(path="/api/x", reason="db:down")
    store.record_idempotency_event(scope="file_upload", action="replay")
    store.record_llm_tokens(7, 3)
    store.request_count["bad-key"] = 1
    store.conversation_state_load_count["bad-key"] = 1
    store.degradation_count["bad-key"] = 1
    store.idempotency_event_count["bad-key"] = 1

    output = store.exposition()

    assert 'path="/api/files/\\"x\\""' in output
    assert 'pdf_agent_tool_calls_total{tool="merge"} 1' in output
    assert 'direction="input"} 7' in output
    assert metrics_module._normalize_metric_path("/api/files/abc/pages/2") == "/api/files/{file_id}/pages/{page}"
    assert metrics_module._normalize_metric_path("/raw", "/declared/{id}") == "/declared/{id}"


def test_i18n_registry_and_db_session_helpers(monkeypatch: pytest.MonkeyPatch):
    assert "PDF Agent" in i18n.get_system_prompt("zh")
    assert i18n.get_system_prompt("missing") == i18n.PROMPTS["en"]
    assert i18n.get_ui_strings("zh")["done"] == "完成"
    assert i18n.get_ui_strings("missing") == i18n.UI_STRINGS["en"]

    class _Tool(BaseTool):
        def manifest(self) -> ToolManifest:
            return ToolManifest(
                name="duplicate",
                label="Duplicate",
                category="test",
                description="test tool",
                inputs=ToolInputSpec(min=0, max=0),
                outputs=ToolOutputSpec(type="json"),
            )

        def validate(self, params: dict) -> dict:
            return {}

        def run(self, inputs, params, workdir, reporter=None) -> ToolResult:
            return ToolResult(log="ok")

    registry = ToolRegistry()
    first = _Tool()
    second = _Tool()
    registry.register(first)
    registry.register(second)
    assert registry.get("duplicate") is second
    assert "duplicate" in registry
    assert registry.list_all() == [second]

    class _SessionContext:
        async def __aenter__(self):
            return "session"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(db_module, "async_session_factory", lambda: _SessionContext())

    async def consume_session():
        sessions = []
        async for session in db_module.get_session():
            sessions.append(session)
        return sessions

    import asyncio

    assert asyncio.run(consume_session()) == ["session"]


@pytest.mark.asyncio
async def test_metrics_endpoint_and_middleware_records_route_path(monkeypatch: pytest.MonkeyPatch):
    store = metrics_module._Metrics()
    monkeypatch.setattr(metrics_module, "metrics", store)

    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/conversations/abc/messages"),
        scope={"route": SimpleNamespace(path="/api/conversations/{conversation_id}/messages")},
    )

    async def call_next(_request):
        return Response(status_code=201)

    response = await metrics_module.MetricsMiddleware.dispatch(object(), request, call_next)
    endpoint_response = await metrics_module.prometheus_metrics()

    assert response.status_code == 201
    assert "POST:/api/conversations/{conversation_id}/messages:201" in store.request_count
    assert endpoint_response.media_type.startswith("text/plain")


@pytest.mark.asyncio
async def test_api_key_and_rate_limit_middleware_edges(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "auth_mode", "disabled")

    request = SimpleNamespace(
        method="GET",
        headers={},
        query_params={},
        url=SimpleNamespace(path="/api/files"),
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )

    async def ok(_request):
        return Response(status_code=204)

    assert (await middleware.ApiKeyMiddleware.dispatch(object(), request, ok)).status_code == 204

    monkeypatch.setattr(settings, "auth_mode", "required")
    monkeypatch.setattr(settings, "api_key", "x" * 32)
    unauthorized = await middleware.ApiKeyMiddleware.dispatch(object(), request, ok)
    assert unauthorized.status_code == 401

    request.headers = {settings.api_key_header_name: "x" * 32}
    assert (await middleware.ApiKeyMiddleware.dispatch(object(), request, ok)).status_code == 204

    monkeypatch.setattr(settings, "rate_limit_rpm", 0)
    assert (await middleware.RateLimitMiddleware.dispatch(object(), request, ok)).status_code == 204
    monkeypatch.setattr(settings, "rate_limit_rpm", 1)
    request.method = "GET"
    assert middleware._should_rate_limit(request) is False
    request.method = "POST"
    assert middleware._should_rate_limit(request) is True

    limiter = middleware.RateLimitMiddleware.__new__(middleware.RateLimitMiddleware)
    monkeypatch.setattr(middleware.RateLimitMiddleware, "_check_and_record", staticmethod(lambda _key: True))
    limited = await middleware.RateLimitMiddleware.dispatch(limiter, request, ok)
    assert limited.status_code == 429

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    assert middleware._rate_limit_file().endswith("rate_limit.json")
    middleware._save_rate_limit_state({"client": [1.0, 2.0], "bad": ["x"]})
    assert middleware._load_rate_limit_state() == {"client": [1.0, 2.0], "bad": []}

    (settings.data_dir / "rate_limit.json").write_text("[]", encoding="utf-8")
    assert middleware._load_rate_limit_state() == {}

    middleware._save_rate_limit_state({"client": [middleware.time.time()]})
    middleware.RateLimitMiddleware.reset(object())
    assert not (settings.data_dir / "rate_limit.json").exists()

    monkeypatch.setattr(settings, "rate_limit_rpm", 1)
    monkeypatch.setattr(middleware.time, "time", lambda: 100.0)
    middleware._save_rate_limit_state({"client": [99.0]})
    assert middleware.RateLimitMiddleware._check_and_record("client") is True
