"""HTTP contract tests for conversation lifecycle and streaming behavior."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import queue

from pdf_agent.api.agent import router as conversation_router
from pdf_agent.config import settings
from pdf_agent.services.idempotency import IdempotencyDecision


@pytest.fixture()
def conversation_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()

    app = FastAPI()
    app.state.graph = None
    app.state.checkpointer = None
    app.include_router(conversation_router)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_conversation_lifecycle_creates_lists_gets_and_deletes_workdir(conversation_client: TestClient):
    create_response = conversation_client.post("/api/conversations")

    assert create_response.status_code == 200
    created = create_response.json()
    conversation_id = created["id"]
    assert created["messages"] == []
    assert (settings.conversations_dir / conversation_id).exists()

    list_response = conversation_client.get("/api/conversations?page=1&limit=10")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["conversations"]] == [conversation_id]

    get_response = conversation_client.get(f"/api/conversations/{conversation_id}")
    assert get_response.status_code == 200
    assert get_response.json()["state"]["source"] == "history"
    assert get_response.json()["state"]["status"] == "degraded"
    assert "history only" in get_response.json()["state"]["warning"]

    delete_response = conversation_client.delete(f"/api/conversations/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "id": conversation_id}
    assert not (settings.conversations_dir / conversation_id).exists()


def test_conversation_artifact_download_rejects_path_traversal(conversation_client: TestClient):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    response = conversation_client.get(f"/api/conversations/{conversation_id}/artifacts/..%2Fsecret.pdf")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid conversation artifact path"


def test_conversation_artifact_download_uses_content_disposition_for_utf8_names(conversation_client: TestClient):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]
    artifact_dir = settings.conversations_dir / conversation_id / "step_1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "结果.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    response = conversation_client.get(f"/api/conversations/{conversation_id}/artifacts/step_1/%E7%BB%93%E6%9E%9C.pdf")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="pdf";')
    assert "filename*=UTF-8''%E7%BB%93%E6%9E%9C.pdf" in response.headers["content-disposition"]


def test_message_stream_returns_minimal_successful_sse_and_persists_history(conversation_client: TestClient):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    class _SingleTokenGraph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["messages"][0].content == "Summarize this"
            assert input_state["configurable"]["thread_id"] == conversation_id
            assert input_state["configurable"]["run_id"].startswith(f"{conversation_id}:")
            assert config == {"configurable": {"thread_id": conversation_id}}
            assert version == "v2"

            async def _events():
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content="Done")},
                }

            return _events()

    conversation_client.app.state.graph = _SingleTokenGraph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": "Summarize this"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: conversation" in response.text
    assert 'event: token\ndata: {"content": "Done"}' in response.text
    assert "event: done" in response.text

    history = (settings.conversations_dir / conversation_id / ".history.jsonl").read_text(encoding="utf-8")
    assert '"type": "human"' in history
    assert '"content": "Summarize this"' in history
    assert '"type": "ai"' in history
    assert '"content": "Done"' in history


def test_message_stream_uses_chat_model_end_when_provider_does_not_stream_chunks(
    conversation_client: TestClient,
):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    class _NonStreamingGraph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["messages"][0].content == "Reply once"
            assert config == {"configurable": {"thread_id": conversation_id}}
            assert version == "v2"

            async def _events():
                yield {
                    "event": "on_chat_model_end",
                    "data": {"output": SimpleNamespace(content="Final answer")},
                }

            return _events()

    conversation_client.app.state.graph = _NonStreamingGraph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": "Reply once"},
    )

    assert response.status_code == 200
    assert 'event: token\ndata: {"content": "Final answer"}' in response.text
    assert "event: done" in response.text

    history = (settings.conversations_dir / conversation_id / ".history.jsonl").read_text(encoding="utf-8")
    assert '"type": "ai"' in history
    assert '"content": "Final answer"' in history


def test_message_stream_does_not_cancel_slow_first_model_event(conversation_client: TestClient):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    class _SlowFirstTokenGraph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["messages"][0].content == "Wait briefly"

            async def _events():
                await asyncio.sleep(0.3)
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content="Still answered")},
                }

            return _events()

    conversation_client.app.state.graph = _SlowFirstTokenGraph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": "Wait briefly"},
    )

    assert response.status_code == 200
    assert 'event: token\ndata: {"content": "Still answered"}' in response.text
    assert "event: done" in response.text


def test_message_stream_emits_error_sse_redacts_args_and_persists_system_history(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]
    cancelled_run_ids: list[str] = []

    monkeypatch.setattr(
        "pdf_agent.api.agent.cancel_conversation_processes",
        lambda run_id: cancelled_run_ids.append(run_id) or 0,
    )

    class _FailingGraph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["messages"][0].content == "Run failing tool"
            assert config == {"configurable": {"thread_id": conversation_id}}
            assert version == "v2"

            async def _events():
                yield {
                    "event": "on_tool_start",
                    "name": "office_to_pdf",
                    "data": {
                        "input": {
                            "password": "raw-secret",
                            "nested": {"api_key": "raw-key"},
                            "state": "internal-state",
                            "visible": "shown",
                        },
                    },
                }
                raise RuntimeError("engine exploded")

            return _events()

    conversation_client.app.state.graph = _FailingGraph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": "Run failing tool"},
    )

    assert response.status_code == 200
    assert "event: tool_start" in response.text
    assert '"password": "[redacted]"' in response.text
    assert '"api_key": "[redacted]"' in response.text
    assert "raw-secret" not in response.text
    assert "raw-key" not in response.text
    assert "internal-state" not in response.text
    assert '"visible": "shown"' in response.text
    assert 'event: error\ndata: {"message": "engine exploded"}' in response.text
    assert "event: done" in response.text
    assert len(cancelled_run_ids) == 1
    assert cancelled_run_ids[0].startswith(f"{conversation_id}:")

    history = (settings.conversations_dir / conversation_id / ".history.jsonl").read_text(encoding="utf-8")
    assert '"type": "human"' in history
    assert '"content": "Run failing tool"' in history
    assert '"type": "system"' in history
    assert '"content": "engine exploded"' in history
    assert '"status": "ERROR"' in history


def test_message_stream_returns_503_when_agent_graph_is_not_initialized(conversation_client: TestClient):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": "hello"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Agent not initialized"


def test_message_idempotency_in_progress_returns_existing_payload(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]
    conversation_client.app.state.graph = SimpleNamespace()

    class _Idempotency:
        async def acquire(self, **_kwargs):
            return IdempotencyDecision(
                action="in_progress",
                response_payload={"conversation_run_id": "existing-run"},
            )

    monkeypatch.setattr("pdf_agent.api.agent.idempotency_service", _Idempotency())

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "msg-in-progress"},
        json={"message": "hello"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["existing"] == {"conversation_run_id": "existing-run"}


def test_message_idempotency_backend_failure_degrades_to_normal_stream(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]

    class _Idempotency:
        async def acquire(self, **_kwargs):
            raise RuntimeError("idempotency backend down")

    class _Graph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["messages"][0].content == "continue anyway"

            async def _events():
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content="ok")},
                }

            return _events()

    monkeypatch.setattr("pdf_agent.api.agent.idempotency_service", _Idempotency())
    conversation_client.app.state.graph = _Graph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "msg-degrade"},
        json={"message": "continue anyway"},
    )

    assert response.status_code == 200
    assert "X-Idempotency-Key" not in response.headers
    assert 'event: token\ndata: {"content": "ok"}' in response.text


def test_message_stream_emits_tool_progress_artifact_and_idempotency_success(
    conversation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    conversation_id = conversation_client.post("/api/conversations").json()["id"]
    artifact_path = settings.conversations_dir / conversation_id / "step_1" / "out.pdf"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    result_payload = (
        "converted\nResult JSON: "
        '{"log":"converted","meta":{"warning":"careful"},"output_files":["'
        + str(artifact_path)
        + '"],"elapsed_seconds":0.4}'
    )
    progress_queue: queue.Queue = queue.Queue()
    progress_queue.put_nowait({"percent": 66, "message": "halfway"})
    released: list[str] = []

    class _Idempotency:
        def __init__(self) -> None:
            self.processing = []
            self.succeeded = []

        async def acquire(self, **_kwargs):
            return IdempotencyDecision(action="acquired", record_id="record-1")

        async def mark_processing(self, **kwargs):
            self.processing.append(kwargs)

        async def mark_succeeded(self, **kwargs):
            self.succeeded.append(kwargs)

        async def mark_failed(self, **kwargs):  # pragma: no cover - success path assertion below
            raise AssertionError(kwargs)

    idem = _Idempotency()

    class _Graph:
        def astream_events(self, input_state, *, config, version):
            assert input_state["configurable"]["thread_id"] == conversation_id

            async def _events():
                yield {
                    "event": "on_tool_start",
                    "name": "office_to_pdf",
                    "data": {"input": {"visible": "yes"}},
                }
                yield {
                    "event": "on_tool_end",
                    "name": "office_to_pdf",
                    "data": {"output": result_payload},
                }

            return _events()

    monkeypatch.setattr("pdf_agent.api.agent.idempotency_service", idem)
    monkeypatch.setattr("pdf_agent.agent.tools_adapter.get_progress_queue", lambda run_id: progress_queue)
    monkeypatch.setattr("pdf_agent.agent.tools_adapter.release_progress_queue", lambda run_id: released.append(run_id))
    conversation_client.app.state.graph = _Graph()

    response = conversation_client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "msg-success"},
        json={"message": "convert this"},
    )

    assert response.status_code == 200
    assert response.headers["X-Idempotency-Key"] == "msg-success"
    assert "event: tool_start" in response.text
    assert 'event: progress\ndata: {"name": "office_to_pdf", "label": "Office To Pdf", "percent": 66' in response.text
    assert "event: artifact" in response.text
    assert f"/api/conversations/{conversation_id}/artifacts/step_1/out.pdf" in response.text
    assert '"warning": "careful"' in response.text
    assert idem.processing[0]["response_payload"]["status"] == "PROCESSING"
    assert idem.succeeded[0]["response_payload"]["status"] == "SUCCESS"
    assert idem.succeeded[0]["response_payload"]["artifacts"] == [
        f"/api/conversations/{conversation_id}/artifacts/step_1/out.pdf"
    ]
    assert len(released) == 1
