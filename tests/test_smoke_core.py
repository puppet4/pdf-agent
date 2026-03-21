"""Smoke coverage for the documented core surfaces."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestCoreSurfaceSmoke:
    def test_app_exposes_core_routes(self):
        from pdf_agent.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}

        assert "/healthz" in paths
        assert "/api/tools" in paths
        assert "/api/files" in paths
        assert "/api/executions" in paths
        assert "/api/agent/chat" in paths
        assert "/api/agent/plans/preview" in paths
        assert "/api/agent/plans/confirm" in paths
        assert "/api/workflows" in paths

    def test_healthz_returns_structured_payload(self):
        from pdf_agent.main import app

        client = TestClient(app)
        response = client.get("/healthz")

        assert response.status_code in {200, 503}
        payload = response.json()
        assert "status" in payload
        assert "database" in payload
        assert "tools_loaded" in payload

    def test_frontend_index_and_react_entry_exist(self):
        index = Path("src/pdf_agent/static/index.html").read_text(encoding="utf-8")
        react_app = Path("src/pdf_agent/static/react-app.js").read_text(encoding="utf-8")

        assert 'id="root"' in index
        assert "react-app.js" in index
        assert "createRoot" in react_app
        assert "Create Execution" in react_app
        assert "Confirm Execution" in react_app
        assert "Create Job" not in react_app
        assert 'className: "tc-icon" }, "E"' in react_app

    def test_models_and_migration_include_documented_core_entities(self):
        from pdf_agent.db import models

        assert hasattr(models, "FileRecord")
        assert hasattr(models, "ExecutionRecord")

        content = Path("alembic/versions/0001_initial_schema.py").read_text(encoding="utf-8")
        assert "'files'" in content
        assert "'executions'" in content

    def test_registry_contains_representative_documented_tools(self):
        from pdf_agent.tools.registry import load_builtin_tools, registry

        if len(registry) == 0:
            load_builtin_tools()

        names = {manifest["name"] for manifest in registry.list_manifests()}
        assert {"merge", "split", "encrypt", "ocr", "metadata_info", "pdf_to_images"}.issubset(names)

    def test_metrics_exposition_uses_execution_naming(self):
        from pdf_agent.api.metrics import metrics

        metrics.record_execution_update(status="SUCCESS", queue_name="light", duration=None)
        body = metrics.exposition()

        assert "pdf_agent_executions_total" in body
        assert "pdf_agent_jobs_total" not in body
