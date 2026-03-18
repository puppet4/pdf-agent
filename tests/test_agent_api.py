"""Tests for the agent API endpoints."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app():
    """Create a test app with mocked graph."""
    from pdf_agent.main import app as _app

    # Mock graph so we don't need a real LLM/DB
    mock_graph = AsyncMock()
    _app.state.graph = mock_graph
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


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


class TestThreadFilesEndpoint:
    def test_list_files_404_for_unknown_thread(self, client):
        response = client.get("/api/agent/threads/nonexistent/files")
        assert response.status_code == 404

    def test_download_404_for_unknown_thread(self, client):
        response = client.get("/api/agent/threads/nonexistent/files/test.pdf")
        assert response.status_code == 404
