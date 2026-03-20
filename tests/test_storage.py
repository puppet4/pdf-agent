"""Tests for local storage safety behavior."""
from __future__ import annotations

import uuid
from pathlib import Path


class TestLocalStorage:
    def test_save_upload_sanitizes_filename(self, tmp_path: Path):
        from pdf_agent.config import settings
        from pdf_agent.storage import LocalStorage

        original_data_dir = settings.data_dir
        try:
            settings.data_dir = tmp_path
            settings.ensure_dirs()

            saved = LocalStorage().save_upload(uuid.uuid4(), "../../escaped.txt", b"payload")

            assert saved.name == "escaped.txt"
            assert saved.parent.parent == settings.upload_dir
            assert saved.exists()
            assert not (settings.data_dir / "escaped.txt").exists()
        finally:
            settings.data_dir = original_data_dir
