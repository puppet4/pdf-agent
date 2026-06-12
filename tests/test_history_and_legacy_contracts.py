"""Coverage for history fallback and legacy compatibility contracts."""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse
import pytest

from pdf_agent.api import legacy
from pdf_agent.config import settings
from pdf_agent.services import conversation_history


def test_history_append_and_load_filters_malformed_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    conversation_dir = tmp_path / "conversation"
    conversation_history.append_history_message(
        conversation_dir=conversation_dir,
        msg_type="human",
        content="hello",
        attachments=[{"name": "a.pdf"}, "bad"],
        files=["/tmp/a.pdf", "", 123],
        meta={"status": "ok"},
    )
    path = conversation_history.history_file_path(conversation_dir)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("{bad json}\n")
        fh.write(json_dump(["not", "dict"]) + "\n")
        fh.write(json_dump({"type": 123, "content": "bad"}) + "\n")
        fh.write(json_dump({"type": "ai", "content": 42}) + "\n")

    loaded = conversation_history.load_history_messages(conversation_dir)

    assert loaded[0] == {
        "type": "human",
        "content": "hello",
        "attachments": [{"name": "a.pdf"}],
        "files": ["/tmp/a.pdf"],
    }
    assert loaded[1] == {"type": "ai", "content": "42"}
    assert conversation_history.load_history_messages(tmp_path / "missing") == []

    def fail_open(*_args, **_kwargs):
        raise OSError("cannot read")

    monkeypatch.setattr(Path, "open", fail_open)
    assert conversation_history.load_history_messages(conversation_dir) == []


def test_history_append_swallows_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fail_mkdir(*_args, **_kwargs):
        raise OSError("cannot mkdir")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    conversation_history.append_history_message(
        conversation_dir=tmp_path / "conversation",
        msg_type="system",
        content="failed",
    )


def test_legacy_headers_payloads_and_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "legacy_api_phase", "warning")
    monkeypatch.setattr(settings, "legacy_api_sunset_date", "2026-12-31")
    monkeypatch.setattr(settings, "legacy_api_migration_url", "https://docs.example.test/migrate")
    settings.conversations_dir.mkdir(parents=True)
    (settings.conversations_dir / "direct_skip").mkdir()
    conversation = settings.conversations_dir / "conversation-a"
    conversation.mkdir()

    headers = legacy._legacy_headers("/replacement")
    assert headers["Deprecation"] == "true"
    assert headers["Warning"].startswith("299")
    assert headers["X-Legacy-Phase"] == "warning"

    items, total, page, limit = legacy._list_legacy_execution_items(page=0, limit=999)
    assert total == 1
    assert page == 1
    assert limit == 200
    assert items[0]["conversation_id"] == "conversation-a"
    assert legacy._validate_legacy_conversation_id("abc_123") == "abc_123"
    with pytest.raises(ValueError):
        legacy._validate_legacy_conversation_id("../bad")

    notice = legacy._legacy_notice_payload(
        endpoint="/api/old",
        replacement="/api/new",
        payload={"ok": True},
        status_code=202,
    )
    assert isinstance(notice, JSONResponse)
    assert notice.status_code == 202

    monkeypatch.setattr(settings, "legacy_api_phase", "sunset")
    sunset = legacy._legacy_sunset_payload(endpoint="/api/old", replacement="/api/new")
    assert sunset.status_code == 410


@pytest.mark.asyncio
async def test_legacy_endpoints_warning_sunset_and_create_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "legacy_api_phase", "warning")
    settings.conversations_dir.mkdir(parents=True)
    monkeypatch.setattr(
        legacy.registry,
        "list_manifests",
        lambda: [{"name": "merge", "label": "Merge", "category": "page_ops"}],
    )

    tools = await legacy.legacy_tools()
    assert tools.status_code == 200
    assert b'"name":"merge"' in tools.body

    listed = await legacy.legacy_executions_list(page=1, limit=20)
    assert listed.status_code == 200
    assert b'"executions"' in listed.body

    invalid = await legacy.legacy_executions_create(
        legacy.LegacyExecutionCreateRequest(conversation_id="../bad")
    )
    assert invalid.status_code == 422

    created = await legacy.legacy_executions_create(
        legacy.LegacyExecutionCreateRequest(conversation_id="legacy-1", title="  Imported  ")
    )
    assert created.status_code == 202
    assert (settings.conversations_dir / "legacy-1" / ".title.txt").read_text(encoding="utf-8") == "Imported"

    workflows = await legacy.legacy_workflows()
    assert workflows.status_code == 200
    assert b"conversation-driven" in workflows.body

    monkeypatch.setattr(settings, "legacy_api_phase", "sunset")
    assert (await legacy.legacy_tools()).status_code == 410
    assert (await legacy.legacy_executions_list()).status_code == 410
    assert (await legacy.legacy_executions_create(legacy.LegacyExecutionCreateRequest())).status_code == 410
    assert (await legacy.legacy_workflows()).status_code == 410


def json_dump(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
