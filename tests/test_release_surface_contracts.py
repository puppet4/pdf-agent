from __future__ import annotations

from pathlib import Path


def test_e2e_script_targets_current_conversation_api_contract():
    script = Path("scripts/test_e2e.sh").read_text(encoding="utf-8")

    assert "/api/agent/chat" not in script
    assert "event: thread" not in script
    assert "/api/conversations" in script
    assert "/messages" in script
    assert "X-API-Key" in script


def test_production_compose_enables_production_policy_explicitly():
    compose = Path("docker-compose.prod.yml").read_text(encoding="utf-8")

    assert "PDF_AGENT_ENVIRONMENT: production" in compose
    assert "PDF_AGENT_AUTH_MODE: required" in compose
    assert "PDF_AGENT_LEGACY_API_COMPATIBILITY_MODE: ${PDF_AGENT_LEGACY_API_COMPATIBILITY_MODE:-disabled}" in compose
    assert "PDF_AGENT_LEGACY_API_PHASE: ${PDF_AGENT_LEGACY_API_PHASE:-sunset}" in compose


def test_nginx_config_no_longer_points_to_removed_backend_static_directory():
    nginx_config = Path("nginx/pdf-agent.conf").read_text(encoding="utf-8")

    assert "src/pdf_agent/static" not in nginx_config
    assert "root /var/www/pdf-agent" in nginx_config


def test_production_dockerfile_keeps_single_process_runtime_default():
    dockerfile = Path("Dockerfile.prod").read_text(encoding="utf-8")

    assert '"--workers", "2"' not in dockerfile
    assert '"--workers", "1"' in dockerfile


def test_browser_qa_runner_executes_the_full_playwright_suite_by_default():
    script = Path("qa/browser-e2e/run_local_matrix.sh").read_text(encoding="utf-8")

    assert 'PLAYWRIGHT_ARGS=("tests/tool-matrix.spec.mjs")' not in script
    assert './node_modules/.bin/playwright test "${PLAYWRIGHT_ARGS[@]}"' in script
    assert "uv run python qa/browser-e2e/support/build_fixtures.py" not in script


def test_quality_gate_defaults_to_functional_automation_not_coverage_chasing():
    script = Path("scripts/quality_gate.sh").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'TEST_PROFILE="${PDF_AGENT_TEST_PROFILE:-functional}"' in script
    assert 'coverage_edges: high-coverage branch tests' in pyproject
    assert 'PYTEST_MARK_EXPR="not external_tools and not coverage_edges"' in script
    assert 'PDF_AGENT_TEST_PROFILE=coverage' in script
