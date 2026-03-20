"""Tests for application lifecycle helpers."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock


class TestCleanupExpiredThreads:
    def test_cleanup_expired_threads_removes_checkpoint_state(self, tmp_path: Path):
        from pdf_agent.config import settings
        from pdf_agent.main import _cleanup_expired_threads_with_checkpointer

        original_data_dir = settings.data_dir
        try:
            settings.data_dir = tmp_path
            settings.ensure_dirs()

            expired = settings.threads_dir / "expired-thread"
            expired.mkdir(parents=True, exist_ok=True)
            (expired / "step_0").mkdir()

            checkpointer = AsyncMock()
            removed = asyncio.run(_cleanup_expired_threads_with_checkpointer(checkpointer, thread_ids=["expired-thread"]))

            assert removed == 1
            assert not expired.exists()
            checkpointer.adelete_thread.assert_awaited_once_with("expired-thread")
        finally:
            settings.data_dir = original_data_dir

    def test_cleanup_thread_checkpoints_uses_cached_ids_without_workdirs(self):
        from pdf_agent.main import _cleanup_thread_checkpoints

        checkpointer = AsyncMock()

        removed = asyncio.run(_cleanup_thread_checkpoints(checkpointer, ["expired-thread"]))

        assert removed == 1
        checkpointer.adelete_thread.assert_awaited_once_with("expired-thread")
