from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from pdf_agent.config import settings
from pdf_agent.main import app
from pdf_agent.services.idempotency import IdempotencyDecision, idempotency_service


@pytest.fixture()
def auth_headers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "auth_mode", "required")
    monkeypatch.setattr(settings, "api_key", "integration-release-key-1234567890")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "disable_agent_persistence", True)
    monkeypatch.setattr(settings, "legacy_api_phase", "deprecation")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    return {settings.api_key_header_name: settings.auth_policy.api_key or ""}


@pytest.fixture()
def client(auth_headers: dict[str, str]) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_authentication_rejects_missing_api_key(client: TestClient, auth_headers: dict[str, str]):
    unauthorized = client.post("/api/conversations")
    authorized = client.post("/api/conversations", headers=auth_headers)

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_message_idempotency_replay_returns_cached_stream(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    conversation = client.post("/api/conversations", headers=auth_headers).json()
    conversation_id = conversation["id"]
    app.state.graph = SimpleNamespace()  # replay branch returns before stream iteration

    async def _acquire(**_kwargs):
        return IdempotencyDecision(
            action="replay",
            record_id=None,
            response_payload={"conversation_id": conversation_id, "status": "SUCCESS"},
        )

    monkeypatch.setattr(idempotency_service, "acquire", _acquire)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**auth_headers, "Idempotency-Key": "msg-idem-key-1"},
        json={"message": "hello"},
    )

    assert response.status_code == 200
    assert response.headers.get("X-Idempotency-Replayed") == "true"
    assert "event: idempotency_replay" in response.text
    assert "event: done" in response.text


def test_message_idempotency_conflict_returns_409(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    conversation_id = client.post("/api/conversations", headers=auth_headers).json()["id"]
    app.state.graph = SimpleNamespace()

    async def _acquire(**_kwargs):
        return IdempotencyDecision(action="conflict", message="payload mismatch")

    monkeypatch.setattr(idempotency_service, "acquire", _acquire)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**auth_headers, "Idempotency-Key": "msg-idem-key-2"},
        json={"message": "hello"},
    )

    assert response.status_code == 409
    assert "payload mismatch" in response.text


def test_checkpointer_unavailable_degrades_to_history(
    client: TestClient,
    auth_headers: dict[str, str],
):
    conversation_id = client.post("/api/conversations", headers=auth_headers).json()["id"]
    history_path = settings.conversations_dir / conversation_id / ".history.jsonl"
    history_path.write_text(
        '{"type":"human","content":"hi"}\n{"type":"ai","content":"ok"}\n',
        encoding="utf-8",
    )

    class _FailingGraph:
        async def aget_state(self, _config):
            raise RuntimeError("checkpoint unavailable")

    app.state.graph = _FailingGraph()
    response = client.get(f"/api/conversations/{conversation_id}", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["status"] == "degraded"
    assert payload["state"]["source"] == "history"
    assert payload["messages"][0]["content"] == "hi"


def test_legacy_endpoints_expose_migration_prompt(client: TestClient, auth_headers: dict[str, str]):
    response = client.get("/api/executions?page=1&limit=5", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    assert response.headers.get("X-Replacement-Endpoint") == "/api/conversations?page=1&limit=20"
    payload = response.json()
    assert payload["deprecated"] is True
    assert payload["phase"] in {"deprecation", "warning"}
    assert "migration_url" in payload
