"""End-to-end integration tests — real upload → tool execution → download."""
from __future__ import annotations

import io
import socket
import pytest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

# Minimal valid PDF bytes (1-page)
_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
190
%%EOF"""


def _pg_available() -> bool:
    """Check if PostgreSQL is reachable on localhost:5432."""
    try:
        s = socket.create_connection(("localhost", 5432), timeout=1)
        s.close()
        return True
    except OSError:
        return False


requires_db = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not available")


@pytest.fixture()
def app():
    from pdf_agent.main import app as _app
    from pdf_agent.tools.registry import load_builtin_tools, registry
    from pdf_agent.config import settings
    # Ensure storage dirs exist
    settings.ensure_dirs()
    # Load tools if not already loaded
    if len(registry) == 0:
        load_builtin_tools()
    mock_graph = AsyncMock()
    _app.state.graph = mock_graph
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


class TestE2EToolRun:
    """Tests that exercise the full upload → direct tool run → download pipeline."""

    @requires_db
    def test_upload_and_get_metadata(self, client):
        """Upload a PDF then run metadata_info tool on it."""
        # 1. Upload
        resp = client.post(
            "/api/files",
            files={"file": ("test.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
        )
        assert resp.status_code == 200
        file_id = resp.json()["id"]
        assert file_id

        # 2. Run metadata_info tool directly
        resp2 = client.post(
            "/api/tools/metadata_info/run",
            json={"file_ids": [file_id], "params": {}},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["tool"] == "metadata_info"
        assert data["status"] == "success"
        assert "log" in data

    @requires_db
    def test_upload_and_rotate(self, client):
        """Upload a PDF then rotate it."""
        # 1. Upload
        resp = client.post(
            "/api/files",
            files={"file": ("rotate_test.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
        )
        assert resp.status_code == 200
        file_id = resp.json()["id"]

        # 2. Rotate
        resp2 = client.post(
            "/api/tools/rotate/run",
            json={"file_ids": [file_id], "params": {"angle": "90", "page_range": "all"}},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["status"] == "success"
        assert len(data["output_files"]) == 1
        assert data["output_files"][0]["filename"] == "rotated.pdf"

        # 3. Download result
        dl_url = data["output_files"][0]["download_url"]
        resp3 = client.get(dl_url)
        assert resp3.status_code == 200
        assert resp3.content[:4] == b"%PDF"

    @requires_db
    def test_upload_list_and_delete(self, client):
        """Upload, list files, then delete."""
        # Upload
        resp = client.post(
            "/api/files",
            files={"file": ("del_test.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
        )
        assert resp.status_code == 200
        file_id = resp.json()["id"]

        # List
        list_resp = client.get("/api/files")
        assert list_resp.status_code == 200
        ids = [f["id"] for f in list_resp.json()["files"]]
        assert file_id in ids

        # Delete
        del_resp = client.delete(f"/api/files/{file_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Verify gone
        list_resp2 = client.get("/api/files")
        ids2 = [f["id"] for f in list_resp2.json()["files"]]
        assert file_id not in ids2

    def test_tool_run_unknown_tool(self, client):
        """Unknown tool returns 404."""
        resp = client.post(
            "/api/tools/nonexistent_tool/run",
            json={"file_ids": [], "params": {}},
        )
        assert resp.status_code == 404

    @requires_db
    def test_tool_run_missing_file(self, client):
        """Non-existent file_id returns 404."""
        resp = client.post(
            "/api/tools/rotate/run",
            json={"file_ids": ["00000000-0000-0000-0000-000000000000"], "params": {"angle": "90"}},
        )
        assert resp.status_code == 404

    def test_healthz_reports_db_status(self, client):
        """Health check endpoint returns DB status field."""
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert "database" in data
        assert "tools_loaded" in data
        assert data["tools_loaded"] >= 34

    def test_workflows_list(self, client):
        """Workflow list returns built-in templates."""
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workflows"]) >= 5
        names = {w["id"] for w in data["workflows"]}
        assert "scan-to-searchable" in names

    def test_workflow_crud(self, client):
        """Create, retrieve, update, and delete a custom workflow."""
        # Create
        resp = client.post("/api/workflows", json={
            "name": "Test WF",
            "description": "A test workflow",
            "prompt_template": "Rotate this PDF by {angle} degrees.",
        })
        assert resp.status_code == 201
        wf_id = resp.json()["id"]

        # Read
        resp2 = client.get(f"/api/workflows/{wf_id}")
        assert resp2.status_code == 200
        assert resp2.json()["name"] == "Test WF"

        # Update
        resp3 = client.put(f"/api/workflows/{wf_id}", json={
            "name": "Updated WF",
            "description": "Updated",
            "prompt_template": "Rotate by {angle}.",
        })
        assert resp3.status_code == 200
        assert resp3.json()["name"] == "Updated WF"

        # Delete
        resp4 = client.delete(f"/api/workflows/{wf_id}")
        assert resp4.status_code == 200
        assert resp4.json()["deleted"] is True

        # Verify gone
        resp5 = client.get(f"/api/workflows/{wf_id}")
        assert resp5.status_code == 404
