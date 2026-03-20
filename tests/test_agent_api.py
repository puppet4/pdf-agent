"""Tests for the agent API endpoints."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app():
    """Create a test app with mocked graph."""
    from pdf_agent.main import app as _app

    # Mock graph so we don't need a real LLM/DB
    mock_graph = AsyncMock()
    _app.state.graph = mock_graph
    _app.state.checkpointer = AsyncMock()
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------

class TestApiKeyMiddleware:
    def test_no_auth_when_api_key_unset(self, client, app):
        """When api_key is empty, all requests pass through."""
        from pdf_agent.config import settings
        original = settings.api_key
        settings.api_key = ""
        try:
            app.state.graph = None  # triggers 503, not 401
            resp = client.post("/api/agent/chat", json={"message": "test"})
            assert resp.status_code == 503  # got through auth, hit graph=None
        finally:
            settings.api_key = original

    def test_rejects_missing_key(self, client):
        """When api_key is set, requests without key get 401."""
        from pdf_agent.config import settings
        original = settings.api_key
        settings.api_key = "secret-key-123"
        try:
            resp = client.post("/api/agent/chat", json={"message": "test"})
            assert resp.status_code == 401
        finally:
            settings.api_key = original

    def test_accepts_valid_key_header(self, client, app):
        """Valid X-API-Key header passes auth."""
        from pdf_agent.config import settings
        original = settings.api_key
        settings.api_key = "secret-key-123"
        try:
            app.state.graph = None
            resp = client.post(
                "/api/agent/chat",
                json={"message": "test"},
                headers={"X-API-Key": "secret-key-123"},
            )
            assert resp.status_code == 503  # passed auth, hit graph=None
        finally:
            settings.api_key = original

    def test_healthz_skips_auth(self):
        """Public paths like /healthz skip authentication."""
        from pdf_agent.config import settings
        from pdf_agent.main import app as _app
        original = settings.api_key
        settings.api_key = "secret-key-123"
        try:
            c = TestClient(_app)
            resp = c.get("/healthz")
            assert resp.status_code == 200
        finally:
            settings.api_key = original


class TestSingleUserSurface:
    def test_auth_routes_removed(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 404

    def test_batch_route_removed(self, client):
        resp = client.post("/api/agent/batch", json={"tool_name": "rotate", "file_ids": []})
        assert resp.status_code == 404

    def test_app_starts_without_openai_key(self):
        from pdf_agent.config import settings
        from pdf_agent.main import app as _app

        original_key = settings.openai_api_key
        try:
            settings.openai_api_key = ""
            _app.state.graph = None
            _app.state.checkpointer = None
            with TestClient(_app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200
                data = resp.json()
                assert data["llm"] == "not configured"
                assert data["agent"] == "not initialized"
                if data["database"] == "ok":
                    assert data["status"] == "ok"
                else:
                    assert data["status"] == "degraded"
                tools_resp = client.get("/api/tools")
                assert tools_resp.status_code == 200
        finally:
            settings.openai_api_key = original_key


class TestRateLimitMiddleware:
    def test_rate_limit_blocks_excess(self, client, app):
        """Exceeding rate_limit_rpm returns 429."""
        from pdf_agent.config import settings
        from pdf_agent.api.middleware import RateLimitMiddleware
        original_rpm = settings.rate_limit_rpm
        settings.rate_limit_rpm = 2

        # Reset rate limiter state from prior tests
        if RateLimitMiddleware._instance:
            RateLimitMiddleware._instance.reset()

        async def mock_stream(*args, **kwargs):
            return
            yield

        app.state.graph.astream_events = mock_stream

        try:
            for _ in range(2):
                resp = client.post("/api/agent/chat", json={"message": "test"})
                assert resp.status_code == 200
            # Third request should be rate limited
            resp = client.post("/api/agent/chat", json={"message": "test"})
            assert resp.status_code == 429
        finally:
            settings.rate_limit_rpm = original_rpm

    def test_rate_limit_disabled(self, client, app):
        """When rate_limit_rpm=0, no rate limiting."""
        from pdf_agent.config import settings
        original_rpm = settings.rate_limit_rpm
        settings.rate_limit_rpm = 0

        async def mock_stream(*args, **kwargs):
            return
            yield

        app.state.graph.astream_events = mock_stream

        try:
            for _ in range(5):
                resp = client.post("/api/agent/chat", json={"message": "test"})
                assert resp.status_code == 200
        finally:
            settings.rate_limit_rpm = original_rpm


class TestChatEndpoint:
    def test_returns_sse_stream(self, client, app):
        """Test that POST /api/agent/chat returns an SSE stream."""
        async def mock_stream(*args, **kwargs):
            yield {"event": "on_chat_model_stream", "name": "test", "data": {"chunk": MagicMock(content="Hello")}}

        app.state.graph.astream_events = mock_stream

        response = client.post(
            "/api/agent/chat",
            json={"message": "Hello"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        # Parse SSE events
        body = response.text
        assert "event: thread" in body
        assert "event: done" in body

    def test_new_thread_id_generated(self, client, app):
        """When no thread_id provided, a new one is generated."""
        async def mock_stream(*args, **kwargs):
            return
            yield  # make it an async generator

        app.state.graph.astream_events = mock_stream

        response = client.post(
            "/api/agent/chat",
            json={"message": "test"},
        )
        body = response.text
        # Find the thread event
        for line in body.split("\n"):
            if line.startswith("data:") and "thread_id" in line:
                data = json.loads(line[5:].strip())
                assert "thread_id" in data
                assert len(data["thread_id"]) > 0
                break

    def test_error_when_graph_not_initialized(self):
        """When graph is None, should return 503."""
        from pdf_agent.main import app as _app
        _app.state.graph = None
        client = TestClient(_app)

        response = client.post(
            "/api/agent/chat",
            json={"message": "test"},
        )
        assert response.status_code == 503

    def test_invalid_file_id_does_not_leave_empty_thread(self, client, app, tmp_path: Path):
        """A validation failure should not create an empty thread directory."""
        from pdf_agent.config import settings

        original_data_dir = settings.data_dir
        try:
            settings.data_dir = tmp_path
            settings.ensure_dirs()

            response = client.post(
                "/api/agent/chat",
                json={"thread_id": "bad-thread", "message": "test", "file_ids": ["not-a-uuid"]},
            )

            assert response.status_code == 422
            assert not (settings.threads_dir / "bad-thread").exists()
        finally:
            settings.data_dir = original_data_dir

    def test_tool_start_filters_internal_keys(self, client, app):
        """tool_start SSE event must not leak state or tool_call_id."""
        async def mock_stream(*args, **kwargs):
            yield {
                "event": "on_tool_start",
                "name": "rotate",
                "data": {"input": {"angle": "90", "state": {"big": "data"}, "tool_call_id": "x"}},
            }

        app.state.graph.astream_events = mock_stream

        response = client.post("/api/agent/chat", json={"message": "test"})
        body = response.text

        # Find the tool_start event data
        for line in body.split("\n"):
            if '"tool_start"' not in line and "tool_start" not in line:
                continue
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                assert "state" not in data.get("args", {})
                assert "tool_call_id" not in data.get("args", {})
                assert data["args"]["angle"] == "90"
                break

    def test_tool_end_returns_download_urls(self, client, app):
        """tool_end SSE event should return download URLs, not raw paths."""
        async def mock_stream(*args, **kwargs):
            yield {
                "event": "on_tool_end",
                "name": "rotate",
                "data": {"output": "Rotated\nOutput files: ['/data/threads/t1/step_0/rotated.pdf']"},
            }

        app.state.graph.astream_events = mock_stream

        response = client.post("/api/agent/chat", json={"thread_id": "t1", "message": "test"})
        body = response.text

        for line in body.split("\n"):
            if line.startswith("data:") and "tool_end" not in line and "rotated" in line:
                data = json.loads(line[5:].strip())
                if "files" in data:
                    assert data["files"][0] == "/api/agent/threads/t1/files/step_0/rotated.pdf"
                    break

    def test_invalid_file_id_returns_422(self, client):
        response = client.post(
            "/api/agent/chat",
            json={"message": "test", "file_ids": ["not-a-uuid"]},
        )
        assert response.status_code == 422

    def test_missing_uploaded_file_returns_404(self, client):
        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def execute(self, *args, **kwargs):
                return FakeResult()

        class FakeSessionFactory:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        from pdf_agent.api import agent as agent_api
        original_factory = agent_api.async_session_factory
        agent_api.async_session_factory = lambda: FakeSessionFactory()
        try:
            response = client.post(
                "/api/agent/chat",
                json={"message": "test", "file_ids": ["00000000-0000-0000-0000-000000000000"]},
            )
        finally:
            agent_api.async_session_factory = original_factory

        assert response.status_code == 404

    def test_progress_queue_released_when_stream_is_cancelled(self, client, app):
        from pdf_agent.agent.tools_adapter import _progress_queues

        class CancelledStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise asyncio.CancelledError()

        app.state.graph.astream_events = lambda *args, **kwargs: CancelledStream()

        response = client.post("/api/agent/chat", json={"thread_id": "cancelled-thread", "message": "test"})

        assert response.status_code == 200
        assert "cancelled-thread" not in _progress_queues


class TestThreadFilesEndpoint:
    def test_list_files_404_for_unknown_thread(self, client):
        response = client.get("/api/agent/threads/nonexistent/files")
        assert response.status_code == 404

    def test_download_404_for_unknown_thread(self, client):
        response = client.get("/api/agent/threads/nonexistent/files/step_0/test.pdf")
        assert response.status_code == 404

    def test_list_files_returns_download_urls(self, client, tmp_path):
        """list_thread_files should include download_url field."""
        from pdf_agent.config import settings

        thread_id = "test-thread-urls"
        thread_dir = settings.threads_dir / thread_id / "step_0"
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "output.pdf").write_bytes(b"%PDF-test")

        try:
            response = client.get(f"/api/agent/threads/{thread_id}/files")
            assert response.status_code == 200
            data = response.json()
            assert len(data["files"]) == 1
            assert data["files"][0]["download_url"] == f"/api/agent/threads/{thread_id}/files/step_0/output.pdf"
            assert "path" not in data["files"][0]  # no raw path exposed
        finally:
            import shutil
            shutil.rmtree(settings.threads_dir / thread_id, ignore_errors=True)

    def test_list_threads_excludes_direct_tool_runs(self, client):
        from pdf_agent.config import settings

        thread_dir = settings.threads_dir / "chat-thread-1"
        direct_dir = settings.threads_dir / "direct_deadbeef"
        thread_dir.mkdir(parents=True, exist_ok=True)
        direct_dir.mkdir(parents=True, exist_ok=True)

        try:
            response = client.get("/api/agent/threads")
            assert response.status_code == 200
            ids = [item["thread_id"] for item in response.json()["threads"]]
            assert "chat-thread-1" in ids
            assert "direct_deadbeef" not in ids
        finally:
            import shutil
            shutil.rmtree(thread_dir, ignore_errors=True)
            shutil.rmtree(direct_dir, ignore_errors=True)


class TestThreadDeletion:
    def test_delete_thread_cleans_checkpointer(self, client, app):
        from pdf_agent.config import settings

        thread_id = "thread-delete-test"
        thread_dir = settings.threads_dir / thread_id
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "step_0").mkdir(exist_ok=True)

        response = client.delete(f"/api/agent/threads/{thread_id}")

        assert response.status_code == 200
        assert not thread_dir.exists()
        app.state.checkpointer.adelete_thread.assert_awaited_once_with(thread_id)
