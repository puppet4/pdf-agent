"""Smoke coverage for the documented core surfaces."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool


class TestCoreSurfaceSmoke:
    def test_app_exposes_core_routes(self):
        from pdf_agent.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}

        assert "/healthz" in paths
        assert "/api/files" in paths
        assert "/api/agent/chat" in paths
        assert "/api/agent/threads" in paths
        assert "/api/tools" not in paths
        assert "/api/executions" not in paths
        assert "/api/agent/plans/preview" not in paths
        assert "/api/agent/plans/confirm" not in paths
        assert "/api/workflows" not in paths

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
        assert "<title>PDF Agent</title>" in index
        assert "react-app.js?v=20260321-chat" in index
        assert "app.css?v=20260321-chat" in index
        assert "no-cache, no-store, must-revalidate" in index
        assert "createRoot" in react_app
        assert "上传文件，直接说结果" in react_app
        assert "把 PDF 拖到这里" in react_app
        assert "新建会话" in react_app
        assert "输出结果" in react_app
        assert "Manual Tools" not in react_app
        assert "/api/tools" not in react_app
        assert "/api/workflows" not in react_app
        assert "/api/executions" not in react_app
        assert "Create Job" not in react_app
        assert "Preview Plan" not in react_app

    def test_api_docs_are_hidden_by_default(self):
        from pdf_agent.main import app

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_legacy_http_routes_are_removed_from_router(self):
        from pdf_agent.api.router import build_api_router

        paths = {route.path for route in build_api_router().routes if hasattr(route, "path")}

        assert "/api/tools" not in paths
        assert "/api/executions" not in paths
        assert "/api/workflows" not in paths
        assert "/api/agent/plans/preview" not in paths
        assert "/api/agent/plans/confirm" not in paths

    def test_models_and_migration_include_documented_core_entities(self):
        from pdf_agent.db import models

        assert hasattr(models, "FileRecord")
        assert not hasattr(models, "ExecutionRecord")

        content = Path("alembic/versions/0001_initial_schema.py").read_text(encoding="utf-8")
        assert "'files'" in content
        assert "'executions'" not in content

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

    def test_async_db_uses_null_pool(self):
        from pdf_agent.db import engine

        assert isinstance(engine.pool, NullPool)
