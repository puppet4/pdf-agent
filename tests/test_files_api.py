"""Tests for file preview endpoints."""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


class TestFilePreviewEndpoints:
    def test_page_preview_surfaces_pdftoppm_failure(self, monkeypatch, tmp_path: Path):
        from pdf_agent.main import app
        import shutil
        import subprocess

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

        async def fake_get(self, file_id):
            return SimpleNamespace(
                storage_path=str(pdf_path),
                mime_type="application/pdf",
                page_count=1,
            )

        class Result:
            returncode = 1
            stderr = b"boom"

        monkeypatch.setattr("pdf_agent.api.files.FileService.get", fake_get)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pdftoppm")
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

        client = TestClient(app)
        response = client.get(f"/api/files/{uuid.uuid4()}/pages/1")

        assert response.status_code == 500
        assert "pdftoppm failed" in response.json()["detail"]
