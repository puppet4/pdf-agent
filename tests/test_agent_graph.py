"""Tests for the LangGraph agent graph construction and execution."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pdf_agent.agent.state import AgentState, FileInfo, files_reducer


# ---------------------------------------------------------------------------
# State reducer tests
# ---------------------------------------------------------------------------

class TestFilesReducer:
    def test_appends_new_files(self):
        existing = [
            FileInfo(file_id="1", path="/a.pdf", orig_name="a.pdf", mime_type="application/pdf", page_count=1, source="upload"),
        ]
        new = [
            FileInfo(file_id="2", path="/b.pdf", orig_name="b.pdf", mime_type="application/pdf", page_count=2, source="rotate"),
        ]
        result = files_reducer(existing, new)
        assert len(result) == 2
        assert result[0]["path"] == "/a.pdf"
        assert result[1]["path"] == "/b.pdf"

    def test_deduplicates_by_path(self):
        existing = [
            FileInfo(file_id="1", path="/a.pdf", orig_name="a.pdf", mime_type="application/pdf", page_count=1, source="upload"),
        ]
        new = [
            FileInfo(file_id="2", path="/a.pdf", orig_name="a.pdf", mime_type="application/pdf", page_count=1, source="upload"),
        ]
        result = files_reducer(existing, new)
        assert len(result) == 1

    def test_empty_lists(self):
        assert files_reducer([], []) == []


# ---------------------------------------------------------------------------
# Graph output file parsing tests
# ---------------------------------------------------------------------------

class TestParseOutputFiles:
    def test_parses_output_files(self):
        from pdf_agent.agent.graph import _parse_output_files
        result = _parse_output_files("Some log\nOutput files: ['/tmp/out.pdf']\nDone")
        assert result == ["/tmp/out.pdf"]

    def test_returns_empty_on_no_match(self):
        from pdf_agent.agent.graph import _parse_output_files
        assert _parse_output_files("No output here") == []

    def test_handles_multiple_files(self):
        from pdf_agent.agent.graph import _parse_output_files
        result = _parse_output_files("Output files: ['/a.pdf', '/b.pdf']")
        assert result == ["/a.pdf", "/b.pdf"]


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_builds_prompt_with_files(self):
        from pdf_agent.agent.prompt import build_system_prompt
        files = [
            FileInfo(file_id="1", path="/tmp/test.pdf", orig_name="test.pdf",
                     mime_type="application/pdf", page_count=5, source="upload"),
        ]
        prompt = build_system_prompt(files, ["/tmp/test.pdf"])
        assert "test.pdf" in prompt
        assert "application/pdf" in prompt
        assert "Current active files" in prompt

    def test_builds_prompt_without_files(self):
        from pdf_agent.agent.prompt import build_system_prompt
        prompt = build_system_prompt([], [])
        assert "No active files yet" in prompt

    def test_static_prompt_included(self):
        from pdf_agent.agent.prompt import build_system_prompt
        prompt = build_system_prompt([], [])
        assert "PDF Agent" in prompt


# ---------------------------------------------------------------------------
# Conditional edge tests
# ---------------------------------------------------------------------------

class TestShouldContinue:
    def test_continues_with_tool_calls(self):
        from pdf_agent.agent.graph import _should_continue
        mock_msg = MagicMock()
        mock_msg.tool_calls = [{"name": "rotate", "args": {}, "id": "1"}]
        state = {"messages": [mock_msg], "step_counter": 0}
        assert _should_continue(state) == "tools"

    def test_ends_without_tool_calls(self):
        from pdf_agent.agent.graph import _should_continue
        mock_msg = MagicMock()
        mock_msg.tool_calls = []
        state = {"messages": [mock_msg], "step_counter": 0}
        from langgraph.graph import END
        assert _should_continue(state) == END

    def test_ends_at_max_iterations(self):
        from pdf_agent.agent.graph import _should_continue
        mock_msg = MagicMock()
        mock_msg.tool_calls = [{"name": "rotate", "args": {}, "id": "1"}]
        state = {"messages": [mock_msg], "step_counter": 999}
        from langgraph.graph import END
        assert _should_continue(state) == END
