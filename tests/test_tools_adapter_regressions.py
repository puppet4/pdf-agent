"""Regression tests for tool adapter wrapper behavior."""
from __future__ import annotations

import json

import pytest

from pdf_agent.agent import tools_adapter
from pdf_agent.tools._builtins.compress import CompressTool
from pdf_agent.tools.base import ToolResult


@pytest.mark.asyncio
async def test_compress_wrapper_returns_result_payload_with_elapsed_seconds(monkeypatch: pytest.MonkeyPatch):
    tool = CompressTool()
    wrapper = tools_adapter._make_tool_wrapper(tool, tool.manifest())

    async def fake_execute_tool_with_state(**kwargs):
        return ToolResult(
            output_files=[],
            meta={"compressed_size": 123},
            log="Compressed PDF",
        )

    monkeypatch.setattr(tools_adapter, "_execute_tool_with_state", fake_execute_tool_with_state)

    result = await wrapper(state={}, level="medium")

    assert "Error:" not in result
    payload_line = next(line for line in result.splitlines() if line.startswith("Result JSON:"))
    payload = json.loads(payload_line.split("Result JSON:", 1)[1].strip())
    assert payload["log"] == "Compressed PDF"
    assert payload["meta"]["compressed_size"] == 123
    assert isinstance(payload["elapsed_seconds"], float)
    assert payload["elapsed_seconds"] >= 0
