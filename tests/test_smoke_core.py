"""Smoke coverage for the documented core surfaces."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

from pdf_agent.core.page_range import parse_page_range


class TestCoreSurfaceSmoke:
    def test_app_exposes_core_routes(self):
        from pdf_agent.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}

        assert "/healthz" in paths
        assert "/api/files" in paths
        assert "/api/conversations" in paths
        assert "/api/conversations/{conversation_id}" in paths
        assert "/api/conversations/{conversation_id}/messages" in paths
        assert "/api/conversations/{conversation_id}/artifacts" in paths
        assert "/api/tools" in paths
        assert "/api/executions" in paths
        assert "/api/workflows" in paths
        assert "/" not in paths

    def test_healthz_returns_structured_payload(self):
        from pdf_agent.main import app

        client = TestClient(app)
        response = client.get("/healthz")

        assert response.status_code in {200, 503}
        payload = response.json()
        assert "status" in payload
        assert "database" in payload
        assert "tools_loaded" in payload

    def test_backend_no_longer_mounts_embedded_frontend(self):
        from pdf_agent.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/" not in paths
        assert all(path != "/static" for path in paths)

    def test_api_docs_are_hidden_by_default(self):
        from pdf_agent.main import app

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_legacy_http_routes_are_bridged_with_deprecation(self):
        from pdf_agent.api.router import build_api_router

        paths = {route.path for route in build_api_router().routes if hasattr(route, "path")}

        assert "/api/tools" in paths
        assert "/api/executions" in paths
        assert "/api/workflows" in paths
        assert "/api/conversations" in paths
        assert "/api/conversations/{conversation_id}/messages" in paths

        from pdf_agent.main import app
        from pdf_agent.config import settings

        client = TestClient(app)
        tools_response = client.get(
            "/api/tools",
            headers={settings.api_key_header_name: settings.auth_policy.api_key or ""},
        )
        assert tools_response.status_code == 200
        assert tools_response.headers.get("Deprecation") == "true"

        executions_response = client.get(
            "/api/executions",
            headers={settings.api_key_header_name: settings.auth_policy.api_key or ""},
        )
        assert executions_response.status_code == 410
        assert executions_response.headers.get("X-Replacement-Endpoint") == "/api/conversations?page=1&limit=20"

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

    def test_metrics_exposition_uses_conversation_run_naming(self):
        from pdf_agent.api.metrics import metrics

        metrics.record_conversation_run(status="SUCCESS", duration=None)
        body = metrics.exposition()

        assert "pdf_agent_conversation_runs_total" in body
        assert "pdf_agent_executions_total" not in body
        assert "pdf_agent_jobs_total" not in body

    def test_async_db_uses_null_pool(self):
        from pdf_agent.db import engine

        assert isinstance(engine.pool, NullPool)

    def test_parse_page_range_deduplicates_while_preserving_order(self):
        assert parse_page_range("1,1", total_pages=5) == [0]
        assert parse_page_range("1-2,2", total_pages=5) == [0, 1]
        assert parse_page_range("last-2-last", total_pages=5) == [2, 3, 4]
