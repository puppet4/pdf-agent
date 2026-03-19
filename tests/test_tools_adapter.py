"""Tests for tools_adapter: ParamSpec → Pydantic conversion and wrapper execution."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_agent.schemas.tool import ParamSpec, ToolInputSpec, ToolManifest, ToolOutputSpec
from pdf_agent.tools.base import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class DummyTool(BaseTool):
    """A minimal tool for testing the adapter."""

    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="dummy",
            label="Dummy Tool",
            category="test",
            description="A dummy tool for testing",
            inputs=ToolInputSpec(min=1, max=1),
            outputs=ToolOutputSpec(type="pdf"),
            params=[
                ParamSpec(name="text", label="Text", type="string", required=True, description="Some text"),
                ParamSpec(name="count", label="Count", type="int", default=5, min=1, max=100, description="A number"),
                ParamSpec(name="ratio", label="Ratio", type="float", default=0.5, min=0.0, max=1.0, description="A ratio"),
                ParamSpec(name="enabled", label="Enabled", type="bool", default=True, description="Toggle"),
                ParamSpec(name="mode", label="Mode", type="enum", options=["fast", "slow"], default="fast", description="Mode"),
                ParamSpec(name="page_range", label="Pages", type="page_range", default="all", description="Page range"),
            ],
            engine="test",
        )

    def validate(self, params: dict) -> dict:
        return params

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter=None) -> ToolResult:
        out = workdir / "output.pdf"
        out.write_bytes(b"%PDF-dummy")
        return ToolResult(
            output_files=[out],
            meta={"text": params.get("text", ""), "count": params.get("count", 5)},
            log=f"Processed with text={params.get('text')}",
        )


class MultiInputTool(BaseTool):
    """A tool that accepts multiple input files."""

    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name="multi_input",
            label="Multi Input",
            category="test",
            description="Accepts multiple files",
            inputs=ToolInputSpec(min=2, max=10),
            outputs=ToolOutputSpec(type="pdf"),
            params=[],
            engine="test",
        )

    def validate(self, params: dict) -> dict:
        return params

    def run(self, inputs: list[Path], params: dict, workdir: Path, reporter=None) -> ToolResult:
        out = workdir / "merged.pdf"
        out.write_bytes(b"%PDF-merged")
        return ToolResult(output_files=[out], meta={"input_count": len(inputs)}, log="Merged")


@pytest.fixture()
def dummy_registry():
    from pdf_agent.tools.registry import ToolRegistry
    reg = ToolRegistry()
    reg.register(DummyTool())
    reg.register(MultiInputTool())
    return reg


# ---------------------------------------------------------------------------
# Tests: ParamSpec → Pydantic schema generation
# ---------------------------------------------------------------------------

class TestParamToPydantic:
    def test_adapt_creates_tools(self, dummy_registry):
        from pdf_agent.agent.tools_adapter import adapt_all_tools
        tools = adapt_all_tools(dummy_registry)
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "dummy" in names
        assert "multi_input" in names

    def test_args_schema_has_correct_fields(self, dummy_registry):
        from pdf_agent.agent.tools_adapter import _build_args_schema
        tool = dummy_registry.get("dummy")
        schema = _build_args_schema(tool.manifest())
        fields = schema.model_fields
        assert "text" in fields
        assert "count" in fields
        assert "ratio" in fields
        assert "enabled" in fields
        assert "mode" in fields
        assert "page_range" in fields
        # Single input tool should NOT have input_file_paths
        assert "input_file_paths" not in fields

    def test_multi_input_has_file_paths_param(self, dummy_registry):
        from pdf_agent.agent.tools_adapter import _build_args_schema
        tool = dummy_registry.get("multi_input")
        schema = _build_args_schema(tool.manifest())
        assert "input_file_paths" in schema.model_fields

    def test_required_field_has_no_default(self, dummy_registry):
        from pdf_agent.agent.tools_adapter import _build_args_schema
        tool = dummy_registry.get("dummy")
        schema = _build_args_schema(tool.manifest())
        text_field = schema.model_fields["text"]
        assert text_field.is_required()

    def test_optional_field_has_default(self, dummy_registry):
        from pdf_agent.agent.tools_adapter import _build_args_schema
        tool = dummy_registry.get("dummy")
        schema = _build_args_schema(tool.manifest())
        count_field = schema.model_fields["count"]
        assert not count_field.is_required()
        assert count_field.default == 5


# ---------------------------------------------------------------------------
# Tests: Tool wrapper execution
# ---------------------------------------------------------------------------

class TestToolWrapper:
    def test_wrapper_executes_and_returns_result(self, dummy_registry, tmp_path):
        import asyncio
        from pdf_agent.agent.tools_adapter import adapt_all_tools

        tools = adapt_all_tools(dummy_registry)
        dummy_tool = next(t for t in tools if t.name == "dummy")

        # Create a fake input file
        fake_pdf = tmp_path / "input.pdf"
        fake_pdf.write_bytes(b"%PDF-test")

        # Create a fake state
        state = {
            "messages": [],
            "files": [],
            "current_files": [str(fake_pdf)],
            "thread_workdir": str(tmp_path),
            "step_counter": 0,
        }

        # Call underlying coroutine directly (same as custom tool node does)
        result = asyncio.run(dummy_tool.coroutine(
            text="hello",
            state=state,
            tool_call_id="test-call-1",
        ))

        assert "Processed with text=hello" in result
        assert "output.pdf" in result

    def test_wrapper_returns_error_on_missing_input(self, dummy_registry, tmp_path):
        import asyncio
        from pdf_agent.agent.tools_adapter import adapt_all_tools

        tools = adapt_all_tools(dummy_registry)
        dummy_tool = next(t for t in tools if t.name == "dummy")

        state = {
            "messages": [],
            "files": [],
            "current_files": [],  # No files
            "thread_workdir": str(tmp_path),
            "step_counter": 0,
        }

        result = asyncio.run(dummy_tool.coroutine(
            text="hello",
            state=state,
            tool_call_id="test-call-2",
        ))

        assert "Error" in result
        assert "at least 1" in result
