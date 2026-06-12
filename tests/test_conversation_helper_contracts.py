"""Focused tests for conversation API helper contracts."""
from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from fastapi.responses import FileResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from openai import APIStatusError, AuthenticationError, BadRequestError, RateLimitError
import httpx
import pytest

from pdf_agent.api import agent as agent_api
from pdf_agent.config import settings
from pdf_agent.services.idempotency import IdempotencyDecision


def _openai_error(cls, status_code: int):
    response = httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://models.example.test/v1/chat/completions"),
    )
    return cls("model error", response=response, body={"error": "bad"})


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _QueryResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)


class _FileLookupSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _query):
        return _QueryResult(self.rows)


@pytest.mark.asyncio
async def test_resolve_uploaded_files_validates_ids_skips_missing_and_requires_one_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    existing_id = uuid.uuid4()
    missing_storage_id = uuid.uuid4()
    missing_db_id = uuid.uuid4()
    existing_path = tmp_path / "existing.pdf"
    existing_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    rows = [
        SimpleNamespace(
            id=existing_id,
            storage_path=str(existing_path),
            orig_name="existing.pdf",
            mime_type="application/pdf",
            page_count=3,
        ),
        SimpleNamespace(
            id=missing_storage_id,
            storage_path=str(tmp_path / "deleted.pdf"),
            orig_name="deleted.pdf",
            mime_type="application/pdf",
            page_count=1,
        ),
    ]
    monkeypatch.setattr(
        agent_api,
        "async_session_factory",
        lambda: _AsyncSessionContext(_FileLookupSession(rows)),
    )

    resolved = await agent_api._resolve_uploaded_files([
        str(existing_id),
        str(missing_storage_id),
        str(missing_db_id),
    ])

    assert resolved == [
        {
            "file_id": str(existing_id),
            "path": str(existing_path),
            "orig_name": "existing.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "source": "upload",
        }
    ]

    with pytest.raises(HTTPException) as invalid:
        await agent_api._resolve_uploaded_files(["not-a-uuid"])
    assert invalid.value.status_code == 422

    monkeypatch.setattr(
        agent_api,
        "async_session_factory",
        lambda: _AsyncSessionContext(_FileLookupSession([])),
    )
    with pytest.raises(HTTPException) as not_found:
        await agent_api._resolve_uploaded_files([str(existing_id)])
    assert not_found.value.status_code == 404


def test_artifact_resolution_selection_and_message_name_matching(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "conversation-1"
    conversation_dir = settings.conversations_dir / conversation_id
    latest_dir = conversation_dir / "step_10"
    older_dir = conversation_dir / "step_2"
    latest_dir.mkdir(parents=True)
    older_dir.mkdir(parents=True)
    pdf_artifact = latest_dir / "结果.pdf"
    pdf_artifact.write_bytes(sample_pdf.read_bytes())
    text_artifact = older_dir / "note.txt"
    text_artifact.write_text("hello", encoding="utf-8")
    hidden = older_dir / ".hidden.pdf"
    hidden.write_bytes(b"hidden")

    pdf_info = agent_api._artifact_path_to_file_info(conversation_id, "step_10/结果.pdf")
    assert pdf_info["mime_type"] == "application/pdf"
    assert pdf_info["page_count"] == 5
    assert pdf_info["artifact_path"] == "step_10/结果.pdf"

    text_info = agent_api._artifact_path_to_file_info(conversation_id, "step_2/note.txt")
    assert text_info["mime_type"] == "application/octet-stream"
    assert text_info["page_count"] is None

    selected = agent_api._resolve_selected_artifacts(
        conversation_id,
        ["step_10/结果.pdf", "step_10/结果.pdf", "step_2/note.txt"],
    )
    assert [item["orig_name"] for item in selected] == ["结果.pdf", "note.txt"]

    matched = agent_api._resolve_message_named_artifact_paths(
        conversation_dir,
        "请继续处理 note.txt 和 结果.pdf",
        conversation_id=conversation_id,
    )
    assert matched == ["step_10/结果.pdf", "step_2/note.txt"]
    assert agent_api._resolve_message_named_artifact_paths(conversation_dir, "  ") == []
    assert agent_api._artifact_step_sort_key("step_bad/out.pdf") == (-1, "step_bad/out.pdf")

    with pytest.raises(HTTPException) as missing:
        agent_api._artifact_path_to_file_info(conversation_id, "step_10/missing.pdf")
    assert missing.value.status_code == 404


def test_serialization_urls_titles_stats_and_artifact_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "conversation_stats_cache_ttl_sec", 60)
    conversation_id = "conversation-2"
    conversation_dir = settings.conversations_dir / conversation_id
    step_dir = conversation_dir / "step_1"
    hidden_dir = step_dir / ".internal"
    hidden_dir.mkdir(parents=True)
    visible = step_dir / "visible.pdf"
    visible.write_bytes(b"%PDF-1.4\n%%EOF\n")
    hidden = hidden_dir / "hidden.pdf"
    hidden.write_bytes(b"hidden")

    artifact_info = {
        "file_id": "artifact:step_1/visible.pdf",
        "path": str(visible),
        "orig_name": "visible.pdf",
        "mime_type": "application/pdf",
        "page_count": 1,
        "source": "artifact",
        "artifact_path": "step_1/visible.pdf",
    }
    upload_info = {
        "file_id": "upload-1",
        "path": "/uploads/upload-1/original.pdf",
        "orig_name": "original.pdf",
        "mime_type": "application/pdf",
        "page_count": 1,
        "source": "upload",
    }

    assert agent_api._serialize_selected_input(artifact_info, conversation_id)["artifactPath"] == "step_1/visible.pdf"
    assert agent_api._serialize_selected_input(upload_info, conversation_id)["file_id"] == "upload-1"
    assert agent_api._paths_to_download_urls(conversation_id, [str(visible)]) == [
        f"/api/conversations/{conversation_id}/artifacts/step_1/visible.pdf"
    ]
    assert agent_api._paths_to_download_urls(conversation_id, [f"/tmp/{conversation_id}/step_7/out.pdf"]) == [
        f"/api/conversations/{conversation_id}/artifacts/step_7/out.pdf"
    ]
    assert agent_api._paths_to_download_urls(conversation_id, ["/tmp/no-step/out.pdf"]) == []

    assert agent_api._sanitize_conversation_title(" New   Conversation ") == "新会话"
    assert agent_api._sanitize_conversation_title("") == "新会话"
    assert agent_api._sanitize_conversation_title("x" * 60).endswith("…")
    agent_api._write_conversation_title(conversation_dir, "  项目   PDF  ")
    assert agent_api._read_conversation_title(conversation_dir) == "项目 PDF"

    assert agent_api._scan_conversation_stats(conversation_dir) == (1, 1)
    assert agent_api._count_artifacts(conversation_dir) == 1
    cache_file = agent_api._conversation_stats_cache_path(conversation_dir)
    cache_payload = json.loads(cache_file.read_text(encoding="utf-8"))
    cache_payload["step_count"] = 9
    cache_payload["artifact_count"] = 8
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")
    assert agent_api._load_conversation_stats(conversation_dir) == (9, 8)
    assert agent_api._is_user_visible_artifact(visible, step_dir) is True
    assert agent_api._is_user_visible_artifact(hidden, step_dir) is False


def test_conversation_helper_error_and_sanitization_contracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    assert "无法连接模型服务" in agent_api._format_agent_stream_error(httpx.ConnectError("offline"))
    assert "鉴权失败" in agent_api._format_agent_stream_error(_openai_error(AuthenticationError, 401))
    assert "限流" in agent_api._format_agent_stream_error(_openai_error(RateLimitError, 429))
    assert "模型请求无效" in agent_api._format_agent_stream_error(_openai_error(BadRequestError, 400))
    assert "HTTP 502" in agent_api._format_agent_stream_error(_openai_error(APIStatusError, 502))
    assert agent_api._format_agent_stream_error(RuntimeError("")) == "处理失败，请查看后端日志。"

    assert agent_api._sanitize_tool_args(
        {
            "state": "internal",
            "password": "secret",
            "nested": {"api_key": "key"},
            "tokens": ["a", "b"],
            "visible": "ok",
        }
    ) == {
        "password": "[redacted]",
        "nested": {"api_key": "[redacted]"},
        "tokens": "[redacted]",
        "visible": "ok",
    }
    assert agent_api._sanitize_tool_args({"items": [{"password": "x"}, "plain"]}) == {
        "items": [{"password": "[redacted]"}, "plain"]
    }

    assert agent_api._format_tool_label("") == "Tool"
    parsed = SimpleNamespace(log=" finished ", meta={"warning": "careful"}, elapsed_seconds=0.2)
    assert agent_api._tool_client_summary("pdf_to_word", parsed) == {
        "name": "pdf_to_word",
        "label": "Pdf To Word",
        "message": "finished",
        "warning": "careful",
        "elapsed_seconds": 0.2,
    }
    assert agent_api._tool_client_summary("tool", None)["message"] == ""
    assert agent_api._extract_output_files(123) == []
    assert agent_api._extract_output_files('Result JSON: {"output_files":["/tmp/out.pdf"]}') == ["/tmp/out.pdf"]

    with pytest.raises(HTTPException) as invalid_conversation:
        agent_api._validate_conversation_id("../bad", status_code=422)
    assert invalid_conversation.value.status_code == 422
    with pytest.raises(HTTPException):
        agent_api._resolve_conversation_artifact_path("ok", "/absolute.pdf")
    with pytest.raises(HTTPException):
        agent_api._resolve_conversation_artifact_path("ok", "uploads/file.pdf")
    assert agent_api._resolve_conversation_artifact_path("ok", "step_1", "file.pdf").name == "file.pdf"
    assert agent_api._paths_to_download_urls("ok", ["/tmp/random/step_9/out.pdf"]) == [
        "/api/conversations/ok/artifacts/step_9/out.pdf"
    ]

    assert agent_api._is_state_backend_error(ConnectionError("down")) is True
    assert agent_api._is_state_backend_error(RuntimeError("postgres closed pool")) is True
    assert agent_api._is_state_backend_error(RuntimeError("plain")) is False


def test_conversation_filesystem_helper_oserror_edges(tmp_path: Path, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "conversation-edges"
    conversation_dir = settings.conversations_dir / conversation_id
    step_dir = conversation_dir / "step_1"
    step_dir.mkdir(parents=True)
    corrupt_pdf = step_dir / "bad.pdf"
    corrupt_pdf.write_text("not a pdf", encoding="utf-8")
    visible = step_dir / "visible.pdf"
    visible.write_bytes(sample_pdf.read_bytes())

    info = agent_api._artifact_path_to_file_info(conversation_id, "step_1/bad.pdf")
    assert info["page_count"] is None
    assert agent_api._read_conversation_title(conversation_dir) == "新会话"

    title_path = agent_api._conversation_title_path(conversation_dir)
    title_path.write_text("title", encoding="utf-8")
    original_read_text = Path.read_text
    original_write_text = Path.write_text

    def fail_title_read(path: Path, *args, **kwargs):
        if path == title_path:
            raise OSError("cannot read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_title_read)
    assert agent_api._read_conversation_title(conversation_dir) == "新会话"
    monkeypatch.setattr(Path, "read_text", original_read_text)

    def fail_title_write(path: Path, *args, **kwargs):
        if path == title_path:
            raise OSError("cannot write")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_title_write)
    agent_api._write_conversation_title(conversation_dir, "ignored")
    monkeypatch.setattr(Path, "write_text", original_write_text)

    stats_file = agent_api._conversation_stats_cache_path(conversation_dir)
    stats_file.write_text("{bad json}", encoding="utf-8")
    assert agent_api._load_conversation_stats(conversation_dir) == (1, 2)

    original_stat = Path.stat

    def fail_conversation_stat(path: Path, *args, **kwargs):
        if path == conversation_dir:
            raise OSError("cannot stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_conversation_stat)
    assert agent_api._load_conversation_stats(conversation_dir) == (0, 0)
    monkeypatch.setattr(Path, "stat", original_stat)

    def fail_cache_write(path: Path, *args, **kwargs):
        if path == stats_file:
            raise OSError("cannot write cache")
        return original_write_text(path, *args, **kwargs)

    stats_file.unlink(missing_ok=True)
    monkeypatch.setattr(Path, "write_text", fail_cache_write)
    assert agent_api._load_conversation_stats(conversation_dir) == (1, 2)
    monkeypatch.setattr(Path, "write_text", original_write_text)

    original_is_file = Path.is_file

    def fail_is_file(path: Path):
        if path == visible:
            raise OSError("cannot inspect")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", fail_is_file)
    assert agent_api._is_user_visible_artifact(visible, step_dir) is False
    monkeypatch.setattr(Path, "is_file", original_is_file)

    monkeypatch.setattr(agent_api, "_is_user_visible_artifact", lambda *_args: (_ for _ in ()).throw(OSError("bad stat")))
    assert agent_api._list_artifacts(conversation_dir, conversation_id) == []


@pytest.mark.asyncio
async def test_conversation_endpoint_error_branches_and_state_load_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.conversations_dir.mkdir(parents=True)
    assert agent_api._list_conversations_sync() == []

    conversation_id = "conversation-endpoints"
    conversation_dir = settings.conversations_dir / conversation_id
    conversation_dir.mkdir()
    direct = settings.conversations_dir / "direct_skip"
    direct.mkdir()
    assert [item["id"] for item in agent_api._list_conversations_sync()] == [conversation_id]

    monkeypatch.setattr(agent_api, "_serialize_conversation", lambda _entry: (_ for _ in ()).throw(OSError("bad dir")))
    assert agent_api._list_conversations_sync() == []
    monkeypatch.undo()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=SimpleNamespace(), checkpointer=None)))
    monkeypatch.setattr(agent_api, "_load_conversation_messages_from_graph", lambda cid, req: _async_value([{"type": "ai", "content": "ok"}]))
    loaded = await agent_api._load_conversation_messages(conversation_id, request, conversation_dir=conversation_dir)
    assert loaded.source == "checkpointer"
    assert loaded.messages == [{"type": "ai", "content": "ok"}]

    with pytest.raises(HTTPException) as missing_get:
        await agent_api.get_conversation("missing", request)
    assert missing_get.value.status_code == 404
    with pytest.raises(HTTPException) as missing_delete:
        await agent_api.delete_conversation("missing", request)
    assert missing_delete.value.status_code == 404
    with pytest.raises(HTTPException) as missing_artifacts:
        await agent_api.list_conversation_artifacts("missing")
    assert missing_artifacts.value.status_code == 404
    with pytest.raises(HTTPException) as missing_download_conversation:
        await agent_api.download_conversation_artifact("missing", "step_1/out.pdf")
    assert missing_download_conversation.value.status_code == 404
    with pytest.raises(HTTPException) as missing_artifact:
        await agent_api.download_conversation_artifact(conversation_id, "step_1/out.pdf")
    assert missing_artifact.value.status_code == 404

    class _FailingCheckpointer:
        async def adelete_thread(self, _conversation_id: str) -> None:
            raise RuntimeError("checkpoint down")

    request.app.state.checkpointer = _FailingCheckpointer()
    delete_id = "conversation-delete"
    delete_dir = settings.conversations_dir / delete_id
    delete_dir.mkdir()
    deleted = await agent_api.delete_conversation(delete_id, request)
    assert deleted["warning"] == "Checkpoint state could not be removed"

    artifact_dir = conversation_dir / "step_1"
    artifact_dir.mkdir()
    artifact = artifact_dir / "out.pdf"
    artifact.write_bytes(b"%PDF-1.4\n%%EOF\n")
    response = await agent_api.download_conversation_artifact(conversation_id, "step_1/out.pdf", inline=True)
    assert isinstance(response, FileResponse)

    listed = await agent_api.list_conversation_artifacts(conversation_id)
    assert listed["artifacts"][0]["filename"] == "out.pdf"


@pytest.mark.asyncio
async def test_conversation_listing_and_artifact_scan_edge_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    assert agent_api._list_conversations_sync() == []

    empty = await agent_api.list_conversations(page=3, limit=5)
    assert empty == {"conversations": [], "total": 0, "page": 1, "limit": 5}

    conversation_id = "scan-edges"
    conversation_dir = settings.conversations_dir / conversation_id
    step_dir = conversation_dir / "step_1"
    step_dir.mkdir(parents=True)
    (step_dir / "out.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    bad_step = conversation_dir / "step_bad"
    bad_step.mkdir()
    original_is_dir = Path.is_dir

    def fail_step_is_dir(path: Path):
        if path == bad_step:
            raise OSError("cannot inspect step")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", fail_step_is_dir)
    artifacts = agent_api._list_artifacts(conversation_dir, conversation_id)
    assert [item["filename"] for item in artifacts] == ["out.pdf"]


@pytest.mark.asyncio
async def test_create_message_rejects_invalid_idempotency_key(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=object())), headers={
        "Idempotency-Key": "x" * (settings.idempotency_max_key_length + 1),
    })

    with pytest.raises(HTTPException) as invalid:
        await agent_api.create_message("conversation-1", agent_api.MessageCreateRequest(message="hi"), request)
    assert invalid.value.status_code == 400


@pytest.mark.asyncio
async def test_create_message_streams_progress_heartbeat_selected_inputs_and_idempotency_degradation(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "stream-conversation"
    conversation_dir = settings.conversations_dir / conversation_id / "step_1"
    conversation_dir.mkdir(parents=True)
    artifact = conversation_dir / "source.pdf"
    artifact.write_bytes(sample_pdf.read_bytes())
    captured: dict[str, object] = {}
    progress_queue: queue.Queue = queue.Queue()
    progress_queue.put_nowait({"percent": 44, "message": "halfway"})
    released: list[str] = []

    class _Graph:
        def astream_events(self, input_state, *, config, version):
            captured["input_state"] = input_state
            captured["config"] = config
            captured["version"] = version

            class _Iterator:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    return {}

            return _Iterator()

    class _Idempotency:
        def __init__(self) -> None:
            self.succeeded: list[dict[str, object]] = []

        async def acquire(self, **_kwargs):
            return IdempotencyDecision(action="acquired", record_id=uuid.uuid4())

        async def mark_processing(self, **_kwargs):
            raise RuntimeError("processing state down")

        async def mark_succeeded(self, *, record_id, response_code, response_payload):
            self.succeeded.append(response_payload)

        async def mark_failed(self, **_kwargs):
            raise AssertionError("success path should not mark failed")

    event_or_errors: list[object] = [
        {"event": "on_tool_start", "name": "merge", "data": {"input": {"password": "secret", "visible": "ok"}}},
        asyncio.TimeoutError(),
        asyncio.TimeoutError(),
        StopAsyncIteration(),
    ]

    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        item = event_or_errors.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    perf_values = iter([0.0, 0.0, 1.0, 7.0, 8.0])

    import pdf_agent.agent.tools_adapter as tools_adapter_module

    idem = _Idempotency()
    monkeypatch.setattr(agent_api, "idempotency_service", idem)
    monkeypatch.setattr(agent_api.uuid, "uuid4", lambda: SimpleNamespace(hex="runfixed"))
    monkeypatch.setattr(agent_api.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(agent_api.time, "perf_counter", lambda: next(perf_values, 8.0))
    monkeypatch.setattr(tools_adapter_module, "get_progress_queue", lambda run_id: progress_queue)
    monkeypatch.setattr(tools_adapter_module, "release_progress_queue", lambda run_id: released.append(run_id))
    monkeypatch.setattr(agent_api, "append_history_message", lambda **_kwargs: None)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(graph=_Graph())),
        headers={"Idempotency-Key": "stream-key"},
    )
    response = await agent_api.create_message(
        conversation_id,
        agent_api.MessageCreateRequest(message="给 source.pdf 加水印", artifact_paths=["step_1/source.pdf"]),
        request,
    )

    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))

    body = "".join(chunks)
    assert "event: progress" in body
    assert "event: heartbeat" in body
    assert "[redacted]" in body
    input_state = captured["input_state"]
    human_kwargs = input_state["messages"][0].additional_kwargs
    assert human_kwargs["selected_inputs"][0]["artifactPath"] == "step_1/source.pdf"
    assert "preferred_tool: watermark_text" in human_kwargs["normalized_intent_hints"]
    assert idem.succeeded[0]["status"] == "SUCCESS"
    assert released == [f"{conversation_id}:runfixed"]


@pytest.mark.asyncio
async def test_create_message_stream_error_records_history_and_idempotency_failure_degradation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "error-conversation"
    history: list[dict[str, object]] = []
    released: list[str] = []

    class _Graph:
        def astream_events(self, *_args, **_kwargs):
            class _Iterator:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    return {}

            return _Iterator()

    class _Idempotency:
        async def acquire(self, **_kwargs):
            return IdempotencyDecision(action="acquired", record_id=uuid.uuid4())

        async def mark_processing(self, **_kwargs):
            return None

        async def mark_succeeded(self, **_kwargs):
            raise AssertionError("error path should not mark succeeded")

        async def mark_failed(self, **_kwargs):
            raise RuntimeError("failed state down")

    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise RuntimeError("model exploded")

    import pdf_agent.agent.tools_adapter as tools_adapter_module

    monkeypatch.setattr(agent_api, "idempotency_service", _Idempotency())
    monkeypatch.setattr(agent_api.uuid, "uuid4", lambda: SimpleNamespace(hex="errrun"))
    monkeypatch.setattr(agent_api.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(agent_api.time, "perf_counter", lambda: 1.0)
    monkeypatch.setattr(agent_api, "cancel_conversation_processes", lambda _run_id: 2)
    monkeypatch.setattr(agent_api, "append_history_message", lambda **kwargs: history.append(kwargs))
    monkeypatch.setattr(tools_adapter_module, "get_progress_queue", lambda _run_id: queue.Queue())
    monkeypatch.setattr(tools_adapter_module, "release_progress_queue", lambda run_id: released.append(run_id))

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(graph=_Graph())),
        headers={"Idempotency-Key": "error-key"},
    )
    response = await agent_api.create_message(
        conversation_id,
        agent_api.MessageCreateRequest(message="hello"),
        request,
    )

    body_parts: list[str] = []
    async for chunk in response.body_iterator:
        body_parts.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    body = "".join(body_parts)

    assert "event: error" in body
    assert "model exploded" in body
    assert any(item.get("msg_type") == "system" and item.get("meta") == {"status": "ERROR"} for item in history)
    assert released == [f"{conversation_id}:errrun"]


@pytest.mark.asyncio
async def test_create_message_stream_cancellation_records_history_and_marks_idempotency_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "cancel-conversation"
    history: list[dict[str, object]] = []
    failed_payloads: list[dict[str, object]] = []

    class _Graph:
        def astream_events(self, *_args, **_kwargs):
            class _Iterator:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    return {}

            return _Iterator()

    class _Idempotency:
        async def acquire(self, **_kwargs):
            return IdempotencyDecision(action="acquired", record_id=uuid.uuid4())

        async def mark_processing(self, **_kwargs):
            return None

        async def mark_succeeded(self, **_kwargs):
            raise AssertionError("cancel path should not mark succeeded")

        async def mark_failed(self, *, record_id, response_code, error_message, response_payload):
            failed_payloads.append(
                {
                    "response_code": response_code,
                    "error_message": error_message,
                    "response_payload": response_payload,
                }
            )

    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.CancelledError

    import pdf_agent.agent.tools_adapter as tools_adapter_module

    monkeypatch.setattr(agent_api, "idempotency_service", _Idempotency())
    monkeypatch.setattr(agent_api.uuid, "uuid4", lambda: SimpleNamespace(hex="cancelrun"))
    monkeypatch.setattr(agent_api.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(agent_api.time, "perf_counter", lambda: 1.0)
    monkeypatch.setattr(agent_api, "cancel_conversation_processes", lambda _run_id: 1)
    monkeypatch.setattr(agent_api, "append_history_message", lambda **kwargs: history.append(kwargs))
    monkeypatch.setattr(tools_adapter_module, "get_progress_queue", lambda _run_id: queue.Queue())
    monkeypatch.setattr(tools_adapter_module, "release_progress_queue", lambda _run_id: None)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(graph=_Graph())),
        headers={"Idempotency-Key": "cancel-key"},
    )
    response = await agent_api.create_message(
        conversation_id,
        agent_api.MessageCreateRequest(message="hello"),
        request,
    )

    iterator = response.body_iterator.__aiter__()
    first = await iterator.__anext__()
    second = await iterator.__anext__()
    with pytest.raises(asyncio.CancelledError):
        await iterator.__anext__()

    body = "".join(part if isinstance(part, str) else part.decode("utf-8") for part in [first, second])
    assert "event: conversation" in body
    assert '"status": "cancelled"' in body
    assert any(item.get("msg_type") == "system" and item.get("meta") == {"status": "CANCELLED"} for item in history)
    assert failed_payloads[0]["response_code"] == 409
    assert failed_payloads[0]["response_payload"]["status"] == "CANCELLED"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_load_conversation_messages_from_graph_maps_attachments_and_artifacts(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conversation_id = "conversation-3"
    conversation_dir = settings.conversations_dir / conversation_id / "step_1"
    conversation_dir.mkdir(parents=True)
    artifact = conversation_dir / "out.pdf"
    artifact.write_bytes(sample_pdf.read_bytes())
    result_payload = "done\nResult JSON: " + json.dumps({"output_files": [str(artifact)], "meta": {}, "log": "done"})
    graph_state = SimpleNamespace(
        values={
            "messages": [
                HumanMessage(
                    content="hello",
                    additional_kwargs={
                        "selected_inputs": [{"name": "original.pdf"}, {"bad": "ignored"}],
                    },
                ),
                ToolMessage(
                    content=result_payload,
                    tool_call_id="call-1",
                    artifact={"output_files": [str(artifact), "", 123]},
                ),
                AIMessage(content="done"),
            ]
        }
    )

    class _Graph:
        async def aget_state(self, config):
            assert config == {"configurable": {"thread_id": conversation_id}}
            return graph_state

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph=_Graph())))

    messages = await agent_api._load_conversation_messages_from_graph(conversation_id, request)

    assert messages[0]["attachments"] == [{"name": "original.pdf"}]
    assert messages[1]["files"] == [
        f"/api/conversations/{conversation_id}/artifacts/step_1/out.pdf"
    ]
    assert messages[1]["content"] == "done"

    request.app.state.graph = None
    with pytest.raises(RuntimeError):
        await agent_api._load_conversation_messages_from_graph(conversation_id, request)
