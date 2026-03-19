"""Tests for the LangGraph agent graph construction and execution."""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock


from pdf_agent.agent.state import FileInfo, files_reducer


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


# ---------------------------------------------------------------------------
# Tiktoken counter tests
# ---------------------------------------------------------------------------

class TestTiktokenCounter:
    def test_counts_tokens(self):
        from pdf_agent.agent.graph import _tiktoken_counter
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="Hello world")]
        count = _tiktoken_counter(messages)
        assert count > 0  # "Hello world" ~= 2 tokens + 4 overhead

    def test_empty_messages(self):
        from pdf_agent.agent.graph import _tiktoken_counter
        assert _tiktoken_counter([]) == 0

    def test_long_message_more_tokens(self):
        from pdf_agent.agent.graph import _tiktoken_counter
        from langchain_core.messages import HumanMessage
        short = [HumanMessage(content="Hi")]
        long = [HumanMessage(content="Hello world, this is a much longer message with many tokens")]
        assert _tiktoken_counter(long) > _tiktoken_counter(short)


# ---------------------------------------------------------------------------
# SSE helpers tests
# ---------------------------------------------------------------------------

class TestSanitizeToolArgs:
    def test_removes_state_and_tool_call_id(self):
        from pdf_agent.api.agent import _sanitize_tool_args
        args = {"angle": "90", "state": {"big": "data"}, "tool_call_id": "abc"}
        clean = _sanitize_tool_args(args)
        assert clean == {"angle": "90"}

    def test_keeps_normal_args(self):
        from pdf_agent.api.agent import _sanitize_tool_args
        args = {"text": "hello", "font_size": 48}
        assert _sanitize_tool_args(args) == args


class TestPathsToDownloadUrls:
    def test_converts_paths(self):
        from pdf_agent.api.agent import _paths_to_download_urls
        urls = _paths_to_download_urls("t1", ["/data/threads/t1/step_0/out.pdf"])
        assert urls == ["/api/agent/threads/t1/files/step_0/out.pdf"]

    def test_empty_paths(self):
        from pdf_agent.api.agent import _paths_to_download_urls
        assert _paths_to_download_urls("t1", []) == []


# ---------------------------------------------------------------------------
# Thread cleanup tests
# ---------------------------------------------------------------------------

class TestThreadCleanup:
    def test_cleanup_expired_threads(self, tmp_path):
        from pdf_agent.config import settings
        from pdf_agent.storage import storage

        original_dir = settings.threads_dir
        settings.data_dir = tmp_path
        threads_dir = tmp_path / "threads"
        threads_dir.mkdir()

        try:
            # Create an "old" thread dir
            old_thread = threads_dir / "old-thread"
            old_thread.mkdir()
            (old_thread / "step_0").mkdir()
            (old_thread / "step_0" / "out.pdf").write_bytes(b"%PDF")
            # Set mtime to 100 hours ago
            old_time = time.time() - 100 * 3600
            os.utime(old_thread, (old_time, old_time))

            # Create a "recent" thread dir
            new_thread = threads_dir / "new-thread"
            new_thread.mkdir()

            removed = storage.cleanup_expired_threads()
            assert removed == 1
            assert not old_thread.exists()
            assert new_thread.exists()
        finally:
            settings.data_dir = original_dir.parent.parent  # restore

    def test_cleanup_no_threads_dir(self, tmp_path):
        from pdf_agent.config import settings
        from pdf_agent.storage import storage

        original_dir = settings.threads_dir
        settings.data_dir = tmp_path / "nonexistent"

        try:
            assert storage.cleanup_expired_threads() == 0
        finally:
            settings.data_dir = original_dir.parent.parent
