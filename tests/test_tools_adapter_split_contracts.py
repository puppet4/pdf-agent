"""Compatibility contracts for the conservative tools_adapter split."""
from __future__ import annotations

from pdf_agent.agent import tool_execution, tool_protocol, tools_adapter


def test_tools_adapter_reexports_extracted_protocol_and_execution_helpers():
    assert hasattr(tool_protocol, "get_progress_queue")
    assert hasattr(tool_protocol, "release_progress_queue")
    assert hasattr(tool_protocol, "parse_tool_result_payload")
    assert hasattr(tool_protocol, "_raise_for_error_output")
    assert hasattr(tool_execution, "_allowed_state_paths")
    assert hasattr(tool_execution, "_state_file_entries")
    assert hasattr(tool_execution, "_execute_tool_with_state")

    parsed = tools_adapter.parse_tool_result_payload("plain text")
    assert parsed.log == "plain text"
    tools_adapter._raise_for_error_output("not an error")
