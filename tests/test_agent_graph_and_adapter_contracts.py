"""Contract tests for agent graph and LangChain tool adapter behavior."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import queue
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from pdf_agent.agent import graph, tools_adapter
from pdf_agent.core import ErrorCode, PDFAgentError
from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ToolResult
from pdf_agent.tools.registry import ToolRegistry


def _manifest(
    name: str = "fake_tool",
    *,
    inputs_min: int = 1,
    inputs_max: int = 1,
    params: list[ParamSpec] | None = None,
    async_hint: bool = False,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        label="Fake Tool",
        category="test",
        description="Fake tool for tests",
        inputs=ToolInputSpec(min=inputs_min, max=inputs_max),
        outputs=ToolOutputSpec(type="pdf"),
        params=params or [],
        async_hint=async_hint,
    )


class _RecordingTool(BaseTool):
    def __init__(
        self,
        *,
        manifest: ToolManifest | None = None,
        result: ToolResult | None = None,
        run_error: Exception | None = None,
    ) -> None:
        self._manifest = manifest or _manifest()
        self.result = result or ToolResult(log="ok")
        self.run_error = run_error
        self.validated_params: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def manifest(self) -> ToolManifest:
        return self._manifest

    def validate(self, params: dict) -> dict:
        self.validated_params.append(dict(params))
        return dict(params)

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter=None) -> ToolResult:
        self.calls.append({"inputs": inputs, "params": params, "workdir": workdir})
        if reporter is not None:
            reporter(33, "running")
        if self.run_error is not None:
            raise self.run_error
        return self.result


class _StaticLangChainTool:
    def __init__(self, name: str, result: str | Exception) -> None:
        self.name = name
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def coroutine(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _state(sample_pdf: Path, tmp_path: Path, *, current_files: list[str] | None = None) -> dict[str, Any]:
    return {
        "files": [
            {
                "file_id": "file-1",
                "path": str(sample_pdf),
                "orig_name": sample_pdf.name,
                "mime_type": "application/pdf",
                "page_count": 5,
                "source": "upload",
            }
        ],
        "current_files": current_files if current_files is not None else [str(sample_pdf)],
        "conversation_workdir": str(tmp_path / "conversation"),
        "step_counter": 2,
        "configurable": {"thread_id": "conversation-1", "run_id": "conversation-1:run-1"},
    }


def test_param_schema_maps_supported_param_types_and_multifile_validation():
    manifest = _manifest(
        inputs_max=3,
        params=[
            ParamSpec(name="title", label="Title", type="string", required=True),
            ParamSpec(name="count", label="Count", type="int", min=1, max=3, default=2),
            ParamSpec(name="ratio", label="Ratio", type="float", min=0.1, max=0.9),
            ParamSpec(name="enabled", label="Enabled", type="bool", default=True),
            ParamSpec(name="mode", label="Mode", type="enum", options=["a", "b"], default="a"),
            ParamSpec(name="pages", label="Pages", type="page_range"),
            ParamSpec(name="unknown", label="Unknown", type="mystery"),
        ],
    )

    schema = tools_adapter._build_args_schema(manifest)
    parsed = schema(
        title="hello",
        count=3,
        ratio=0.5,
        enabled=False,
        mode="b",
        pages="1-2",
        unknown="value",
        input_file_paths=["/tmp/a.pdf", "/tmp/b.pdf"],
    )

    assert parsed.title == "hello"
    assert parsed.count == 3
    assert parsed.ratio == 0.5
    assert parsed.enabled is False
    assert parsed.mode == "b"
    assert parsed.pages == "1-2"
    assert parsed.unknown == "value"
    assert parsed.input_file_paths == ["/tmp/a.pdf", "/tmp/b.pdf"]
    with pytest.raises(Exception):
        schema(title="hello", count=4)
    with pytest.raises(Exception):
        schema(title="hello", mode="c")

    enum_manifest = _manifest(params=[ParamSpec(name="free", label="Free", type="enum")])
    enum_schema = tools_adapter._build_args_schema(enum_manifest)
    assert enum_schema(free="anything").free == "anything"


def test_allowed_state_paths_and_progress_queue_ttl(monkeypatch: pytest.MonkeyPatch, sample_pdf: Path):
    missing = sample_pdf.parent / "missing.pdf"
    state = {
        "files": [{"path": str(sample_pdf)}, {"path": ""}, "bad"],
        "current_files": [str(missing)],
    }

    assert tools_adapter._allowed_state_paths(state) == {sample_pdf.resolve(), missing.resolve()}

    tools_adapter._progress_queues.clear()
    monkeypatch.setattr(tools_adapter, "time", SimpleNamespace(time=lambda: 1000.0))
    first = tools_adapter.get_progress_queue("run-a")
    same = tools_adapter.get_progress_queue("run-a")
    assert same is first

    tools_adapter._progress_queues["stale"] = (queue.Queue(), 1000.0 - tools_adapter._PROGRESS_TTL_SEC - 1)
    tools_adapter.get_progress_queue("run-b")
    assert "stale" not in tools_adapter._progress_queues
    tools_adapter.release_progress_queue("run-a")
    assert "run-a" not in tools_adapter._progress_queues


def test_result_payload_parser_and_error_output_contracts():
    payload = {
        "log": "finished",
        "meta": {"count": 2},
        "output_files": ["/tmp/out.pdf", "", 12],
        "elapsed_seconds": 1.25,
    }
    parsed = tools_adapter.parse_tool_result_payload(
        "finished\nResult JSON: " + json.dumps(payload)
    )

    assert parsed.log == "finished"
    assert parsed.meta == {"count": 2}
    assert parsed.output_files == ["/tmp/out.pdf"]
    assert parsed.elapsed_seconds == 1.25
    assert tools_adapter.parse_tool_result_payload("plain text").log == "plain text"
    assert tools_adapter.parse_tool_result_payload(
        "Result JSON: " + json.dumps({"meta": "bad", "elapsed_seconds": "bad"})
    ).meta == {}
    assert tools_adapter._state_file_entries([Path("/tmp/a.pdf")])[0]["orig_name"] == "a.pdf"

    with pytest.raises(PDFAgentError) as explicit:
        tools_adapter._raise_for_error_output("Error: [INVALID_PARAMS] bad input")
    assert explicit.value.code == ErrorCode.INVALID_PARAMS
    with pytest.raises(PDFAgentError) as fallback:
        tools_adapter._raise_for_error_output("Error: backend failed")
    assert fallback.value.code == ErrorCode.ENGINE_EXEC_FAILED
    tools_adapter._raise_for_error_output("not an error")


@pytest.mark.asyncio
async def test_execute_tool_validates_inputs_defaults_progress_and_outputs(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_path = tmp_path / "conversation" / "step_2" / "out.pdf"
    manifest = _manifest(
        params=[ParamSpec(name="quality", label="Quality", type="string", default="high")],
    )
    tool = _RecordingTool(manifest=manifest, result=ToolResult(output_files=[output_path], log="done"))
    progress_queue: queue.Queue = queue.Queue(maxsize=1)
    progress_queue.put_nowait({"percent": 1, "message": "already full"})
    callbacks: list[tuple[int, str]] = []
    bound_ids: list[str | None] = []

    def fake_queue(run_id: str) -> queue.Queue:
        assert run_id == "conversation-1:run-1"
        return progress_queue

    @contextmanager
    def fake_bind(run_id: str | None):
        bound_ids.append(run_id)
        yield

    monkeypatch.setattr(tools_adapter, "get_progress_queue", fake_queue)
    monkeypatch.setattr(tools_adapter, "bind_conversation_run_context", fake_bind)

    result = await tools_adapter._execute_tool_with_state(
        tool=tool,
        manifest=manifest,
        state=_state(sample_pdf, tmp_path),
        kwargs={},
        progress_reporter=lambda percent, message: callbacks.append((percent, message)),
    )

    assert result.log == "done"
    assert tool.validated_params == [{"quality": "high"}]
    assert tool.calls[0]["inputs"] == [sample_pdf.resolve()]
    assert tool.calls[0]["workdir"] == output_path.parent
    assert callbacks == [(33, "running")]
    assert bound_ids == ["conversation-1:run-1"]

    truncated_tool = _RecordingTool(
        manifest=_manifest(inputs_min=1, inputs_max=1),
        result=ToolResult(output_files=[output_path], log="done"),
    )
    second_pdf = sample_pdf.parent / "second.pdf"
    second_pdf.write_bytes(sample_pdf.read_bytes())
    truncated_state = _state(sample_pdf, tmp_path, current_files=[str(sample_pdf), str(second_pdf)])
    await tools_adapter._execute_tool_with_state(
        tool=truncated_tool,
        manifest=_manifest(inputs_min=1, inputs_max=1),
        state=truncated_state,
        kwargs={},
    )
    assert truncated_tool.calls[-1]["inputs"] == [sample_pdf.resolve()]


@pytest.mark.asyncio
async def test_execute_tool_rejects_bad_inputs_and_wraps_runtime_errors(
    tmp_path: Path,
    sample_pdf: Path,
):
    tool = _RecordingTool()

    with pytest.raises(PDFAgentError) as missing:
        await tools_adapter._execute_tool_with_state(
            tool=tool,
            manifest=_manifest(inputs_min=2),
            state=_state(sample_pdf, tmp_path, current_files=[]),
            kwargs={},
        )
    assert missing.value.code == ErrorCode.INVALID_INPUT_FILE

    with pytest.raises(PDFAgentError) as outside_input:
        await tools_adapter._execute_tool_with_state(
            tool=tool,
            manifest=_manifest(inputs_max=2),
            state=_state(sample_pdf, tmp_path),
            kwargs={"input_file_paths": [str(tmp_path / "not-selected.pdf")]},
        )
    assert outside_input.value.code == ErrorCode.INVALID_INPUT_FILE

    outside_output = tmp_path / "outside.pdf"
    with pytest.raises(PDFAgentError) as outside:
        await tools_adapter._execute_tool_with_state(
            tool=_RecordingTool(result=ToolResult(output_files=[outside_output])),
            manifest=_manifest(),
            state=_state(sample_pdf, tmp_path),
            kwargs={},
        )
    assert outside.value.code == ErrorCode.OUTPUT_GENERATION_FAILED

    with pytest.raises(PDFAgentError) as wrapped:
        await tools_adapter._execute_tool_with_state(
            tool=_RecordingTool(run_error=RuntimeError("boom")),
            manifest=_manifest(),
            state=_state(sample_pdf, tmp_path),
            kwargs={},
        )
    assert wrapped.value.code == ErrorCode.ENGINE_EXEC_FAILED


@pytest.mark.asyncio
async def test_execute_async_tool_timeout_is_reported(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(tools_adapter.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(PDFAgentError) as exc_info:
        await tools_adapter._execute_tool_with_state(
            tool=_RecordingTool(manifest=_manifest(async_hint=True)),
            manifest=_manifest(async_hint=True),
            state=_state(sample_pdf, tmp_path),
            kwargs={},
        )

    assert exc_info.value.code == ErrorCode.ENGINE_EXEC_TIMEOUT


@pytest.mark.asyncio
async def test_tool_wrapper_formats_errors_and_redacts_sensitive_metadata(
    sample_pdf: Path,
    tmp_path: Path,
):
    wrapper = tools_adapter._make_tool_wrapper(
        _RecordingTool(
            result=ToolResult(
                output_files=[],
                meta={"owner_password": "secret", "safe": "value"},
                log="done",
            )
        ),
        _manifest(),
    )

    result = await wrapper(state=_state(sample_pdf, tmp_path))

    assert "secret" not in result
    assert "owner_password" not in result
    assert "safe" in result
    payload = json.loads(next(line for line in result.splitlines() if line.startswith("Result JSON:")).split(":", 1)[1])
    assert payload["meta"] == {"safe": "value"}

    error_wrapper = tools_adapter._make_tool_wrapper(_RecordingTool(run_error=PDFAgentError(ErrorCode.INVALID_PARAMS, "bad")), _manifest())
    assert await error_wrapper(state=_state(sample_pdf, tmp_path)) == "Error: [INVALID_PARAMS] bad"

    unexpected = tools_adapter._make_tool_wrapper(_RecordingTool(), _manifest())
    original_execute = tools_adapter._execute_tool_with_state
    try:
        tools_adapter._execute_tool_with_state = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected"))
        assert await unexpected(state=_state(sample_pdf, tmp_path)) == "Error: [ENGINE_EXEC_FAILED] unexpected"
    finally:
        tools_adapter._execute_tool_with_state = original_execute


@pytest.mark.asyncio
async def test_adapted_tool_map_cache_and_public_invoke(
    tmp_path: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    registry = ToolRegistry()
    registry.register(_RecordingTool(manifest=_manifest(name="one")))
    first = tools_adapter.get_adapted_tool_map(registry)
    second = tools_adapter.get_adapted_tool_map(registry)
    assert second is first

    registry.register(_RecordingTool(manifest=_manifest(name="two")))
    refreshed = tools_adapter.get_adapted_tool_map(registry)
    assert refreshed is not first
    assert sorted(refreshed) == ["one", "two"]

    with pytest.raises(PDFAgentError) as unknown:
        await tools_adapter.invoke_adapted_tool(
            registry=registry,
            tool_name="missing",
            input_paths=[sample_pdf],
            params={},
            conversation_workdir=tmp_path,
            step_counter=0,
            conversation_id="conversation-1",
        )
    assert unknown.value.code == ErrorCode.INVALID_PARAMS

    output_path = tmp_path / "step_0" / "out.pdf"
    fake_tool = _StaticLangChainTool(
        "fake",
        "done\nResult JSON: " + json.dumps({"log": "done", "output_files": [str(output_path)], "meta": {"ok": True}}),
    )
    monkeypatch.setattr(tools_adapter, "get_adapted_tool_map", lambda _registry: {"fake": fake_tool})
    updates: list[tuple[int, str]] = []
    parsed = await tools_adapter.invoke_adapted_tool(
        registry=registry,
        tool_name="fake",
        input_paths=[sample_pdf],
        params={"quality": "high"},
        conversation_workdir=tmp_path,
        step_counter=0,
        conversation_id="conversation-1",
        progress_reporter=lambda percent, message: updates.append((percent, message)),
    )

    assert parsed.output_files == [str(output_path)]
    assert fake_tool.calls[0]["input_file_paths"] == [str(sample_pdf.resolve())]
    assert fake_tool.calls[0]["quality"] == "high"
    assert fake_tool.calls[0]["state"]["files"][0]["source"] == "conversation_run"


def test_graph_token_counter_handles_encoder_fallback_and_mixed_content(monkeypatch: pytest.MonkeyPatch):
    class FakeEncoder:
        def encode(self, text: str, disallowed_special=()) -> list[str]:
            return text.split()

    def fail_for_model(_model: str):
        raise KeyError("unknown model")

    monkeypatch.setattr(graph, "_encoder", None)
    monkeypatch.setattr(graph.tiktoken, "encoding_for_model", fail_for_model)
    monkeypatch.setattr(graph.tiktoken, "get_encoding", lambda _name: FakeEncoder())

    count = graph._tiktoken_counter([
        HumanMessage(content="one two"),
        SimpleNamespace(content=["three four", {"text": "five six"}, {"ignored": "seven"}]),
        object(),
    ])

    assert count == 22


@pytest.mark.asyncio
async def test_agent_node_trims_summarizes_and_records_token_usage(monkeypatch: pytest.MonkeyPatch):
    class FakeModel:
        def __init__(self, responses: list[AIMessage]) -> None:
            self.responses = responses
            self.calls: list[list[Any]] = []

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            self.calls.append(messages)
            return self.responses.pop(0)

    usage_response = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    )
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(graph.metrics, "record_llm_tokens", lambda in_tokens, out_tokens: recorded.append((in_tokens, out_tokens)))
    monkeypatch.setattr(graph, "build_system_prompt", lambda **_kwargs: "system")
    monkeypatch.setattr(graph, "prepare_messages_for_model", lambda messages: messages)
    monkeypatch.setattr(graph, "_tiktoken_counter", lambda _messages: graph.MAX_HISTORY_TOKENS + 1)
    monkeypatch.setattr(graph, "trim_messages", lambda messages, **_kwargs: messages[-1:])

    trim_model = FakeModel([usage_response])
    trim_node = graph._make_agent_node(trim_model)
    trim_update = await trim_node({"messages": [HumanMessage(content="old"), HumanMessage(content="latest")]})

    assert trim_update == {"messages": [usage_response]}
    assert len(trim_model.calls[0]) == 2
    assert recorded == [(11, 3)]

    summary_response = AIMessage(content="summary")
    metadata_response = AIMessage(content="ok", response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 5}})
    monkeypatch.setattr(graph, "_tiktoken_counter", lambda _messages: graph.SUMMARY_THRESHOLD + 1)
    summary_model = FakeModel([summary_response, metadata_response])
    summary_node = graph._make_agent_node(summary_model)
    update = await summary_node(
        {"messages": [HumanMessage(content=f"old {index}") for index in range(5)]}
    )

    assert update == {"messages": [metadata_response]}
    assert len(summary_model.calls) == 2
    assert "[Earlier conversation summary: summary]" in summary_model.calls[1][1].content
    assert recorded[-1] == (7, 5)


@pytest.mark.asyncio
async def test_agent_node_uses_recent_messages_when_summarization_fails(monkeypatch: pytest.MonkeyPatch):
    class FailingSummaryModel:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("summary failed")
            return AIMessage(content="ok")

    monkeypatch.setattr(graph, "build_system_prompt", lambda **_kwargs: "system")
    monkeypatch.setattr(graph, "prepare_messages_for_model", lambda messages: messages)
    monkeypatch.setattr(graph, "_tiktoken_counter", lambda _messages: graph.SUMMARY_THRESHOLD + 1)

    model = FailingSummaryModel()
    node = graph._make_agent_node(model)
    result = await node({"messages": [HumanMessage(content=f"msg {index}") for index in range(5)]})

    assert result["messages"][0].content == "ok"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_tool_node_handles_unknown_success_exception_and_state_updates(tmp_path: Path, sample_pdf: Path):
    output_pdf = tmp_path / "out.pdf"
    output_pdf.write_bytes(sample_pdf.read_bytes())
    output_txt = tmp_path / "note.txt"
    output_txt.write_text("hello", encoding="utf-8")
    result_payload = "ok\nResult JSON: " + json.dumps(
        {"log": "ok", "output_files": [str(output_pdf), str(output_txt)], "meta": {"pages": 5}, "elapsed_seconds": 0.5}
    )
    good_tool = _StaticLangChainTool("known", result_payload)
    failing_tool = _StaticLangChainTool("broken", RuntimeError("boom"))
    node = graph._make_tool_node([good_tool, failing_tool], ToolRegistry())
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "missing", "args": {}, "id": "call-missing"},
            {"name": "known", "args": {"value": 1}, "id": "call-known"},
            {"name": "broken", "args": {}, "id": "call-broken"},
        ],
    )

    update = await node({"messages": [message], "step_counter": 4, "current_files": []})

    assert update["step_counter"] == 6
    assert [msg.tool_call_id for msg in update["messages"]] == ["call-missing", "call-known", "call-broken"]
    assert "unknown tool" in update["messages"][0].content
    assert update["messages"][1].artifact["output_files"] == [str(output_pdf), str(output_txt)]
    assert update["messages"][1].artifact["meta"] == {"pages": 5}
    assert update["messages"][2].content == "Error: boom"
    assert update["current_files"] == [str(output_pdf), str(output_txt)]
    assert {file["orig_name"] for file in update["files"]} == {"out.pdf", "note.txt"}
    assert good_tool.calls[0]["state"]["step_counter"] == 4
    assert good_tool.calls[0]["tool_call_id"] == "call-known"

    assert await node({"messages": [AIMessage(content="no calls")], "step_counter": 1}) == {}


def test_graph_page_count_continue_and_build_graph(monkeypatch: pytest.MonkeyPatch, sample_pdf: Path):
    assert graph._get_page_count(sample_pdf) == 5
    assert graph._get_page_count(sample_pdf.parent / "missing.pdf") is None

    assert graph._should_continue({"messages": [AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}])], "step_counter": 0}) == "tools"
    assert graph._should_continue({"messages": [AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}])], "step_counter": graph.settings.agent_max_iterations}) == graph.END
    assert graph._should_continue({"messages": [AIMessage(content="done")], "step_counter": 0}) == graph.END

    class FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def bind_tools(self, tools: list[Any], parallel_tool_calls: bool) -> str:
            assert parallel_tool_calls is False
            return f"bound:{len(tools)}"

    class FakeStateGraph:
        def __init__(self, state_type: Any) -> None:
            self.state_type = state_type
            self.nodes: dict[str, Any] = {}
            self.entry = ""
            self.conditional = None
            self.edges: list[tuple[str, str]] = []

        def add_node(self, name: str, node: Any) -> None:
            self.nodes[name] = node

        def set_entry_point(self, name: str) -> None:
            self.entry = name

        def add_conditional_edges(self, *args: Any) -> None:
            self.conditional = args

        def add_edge(self, start: str, end: str) -> None:
            self.edges.append((start, end))

        def compile(self, checkpointer=None) -> dict[str, Any]:
            return {
                "state_type": self.state_type,
                "nodes": self.nodes,
                "entry": self.entry,
                "conditional": self.conditional,
                "edges": self.edges,
                "checkpointer": checkpointer,
            }

    monkeypatch.setattr(graph, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(graph, "StateGraph", FakeStateGraph)
    monkeypatch.setattr(graph, "get_adapted_tool_map", lambda _registry: {"known": _StaticLangChainTool("known", "ok")})
    monkeypatch.setattr(graph.settings, "openai_base_url", "https://models.example.test")

    compiled = graph.build_graph(checkpointer="checkpoint", tool_registry=ToolRegistry())

    assert compiled["entry"] == "agent"
    assert set(compiled["nodes"]) == {"agent", "tools"}
    assert compiled["edges"] == [("tools", "agent")]
    assert compiled["conditional"][2] == {"tools": "tools", graph.END: graph.END}
    assert compiled["checkpointer"] == "checkpoint"
