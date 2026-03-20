"""Tests for tools API edge cases."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


class TestToolResultDownloads:
    def test_nested_result_download_path_is_served(self, tmp_path: Path):
        from pdf_agent.config import settings
        from pdf_agent.main import app

        original_data_dir = settings.data_dir
        try:
            settings.data_dir = tmp_path
            settings.ensure_dirs()

            nested = settings.threads_dir / "direct_1234" / "step_0"
            nested.mkdir(parents=True, exist_ok=True)
            output = nested / "result.pdf"
            output.write_bytes(b"%PDF-1.4\n%%EOF")

            client = TestClient(app)
            response = client.get("/api/tools/results/direct_1234/step_0/result.pdf")

            assert response.status_code == 200
            assert response.content.startswith(b"%PDF")
        finally:
            settings.data_dir = original_data_dir

    def test_download_zip_includes_nested_result_files(self, tmp_path: Path):
        from pdf_agent.config import settings
        from pdf_agent.main import app

        original_data_dir = settings.data_dir
        try:
            settings.data_dir = tmp_path
            settings.ensure_dirs()

            nested = settings.threads_dir / "direct_5678" / "step_1"
            nested.mkdir(parents=True, exist_ok=True)
            output = nested / "nested.txt"
            output.write_text("payload")

            client = TestClient(app)
            response = client.post(
                "/api/tools/download-zip",
                json={"urls": ["/api/tools/results/direct_5678/step_1/nested.txt"]},
            )

            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                assert zf.namelist() == ["nested.txt"]
                assert zf.read("nested.txt") == b"payload"
        finally:
            settings.data_dir = original_data_dir
