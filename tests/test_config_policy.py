from __future__ import annotations

import pytest

from pdf_agent.config import Settings


def test_default_required_auth_uses_ephemeral_non_production_api_key():
    cfg = Settings(environment="development", auth_mode="required", api_key="__PDF_AGENT_API_KEY_UNSET__")
    policy = cfg.auth_policy

    assert policy.enabled is True
    assert policy.mode == "required"
    assert policy.api_key is not None
    assert policy.api_key != "dev-local-api-key"
    assert "ephemeral" in policy.reason


def test_production_requires_explicit_api_key():
    cfg = Settings(environment="production", auth_mode="required", api_key="__PDF_AGENT_API_KEY_UNSET__")

    with pytest.raises(ValueError, match="requires PDF_AGENT_API_KEY"):
        _ = cfg.auth_policy


def test_production_rejects_weak_api_key():
    cfg = Settings(environment="production", auth_mode="required", api_key="changeme")

    with pytest.raises(ValueError, match="weak/default"):
        _ = cfg.auth_policy


def test_production_rejects_optional_and_disabled_modes():
    optional_cfg = Settings(environment="production", auth_mode="optional", api_key="x" * 32)
    disabled_cfg = Settings(environment="production", auth_mode="disabled", api_key="x" * 32)

    with pytest.raises(ValueError, match="not allowed"):
        _ = optional_cfg.auth_policy
    with pytest.raises(ValueError, match="not allowed"):
        _ = disabled_cfg.auth_policy


def test_validate_runtime_rejects_short_api_key():
    cfg = Settings(environment="development", auth_mode="required", api_key="short", min_api_key_length=16)

    with pytest.raises(ValueError, match="too short"):
        cfg.validate_runtime()


def test_default_release_surface_is_chat_first_without_legacy_bridge():
    cfg = Settings(_env_file=None)

    assert cfg.legacy_api_compatibility_mode == "disabled"
    assert cfg.legacy_api_phase == "sunset"


def test_metrics_is_exempt_from_api_key_by_default():
    cfg = Settings(_env_file=None)

    assert "/healthz" in cfg.auth_exempt_path_set
    assert "/metrics" in cfg.auth_exempt_path_set


def test_optional_and_disabled_auth_policy_edges():
    disabled = Settings(environment="development", auth_mode="disabled")
    assert disabled.auth_policy.enabled is False

    optional_empty = Settings(environment="test", auth_mode="optional", api_key="")
    assert optional_empty.auth_policy.enabled is False

    optional_strong = Settings(environment="test", auth_mode="optional", api_key="x" * 32)
    assert optional_strong.auth_policy.enabled is True

    optional_weak = Settings(environment="test", auth_mode="optional", api_key="changeme")
    with pytest.raises(ValueError, match="weak/default"):
        _ = optional_weak.auth_policy

    optional_short = Settings(environment="test", auth_mode="optional", api_key="short", min_api_key_length=16)
    with pytest.raises(ValueError, match="too short"):
        _ = optional_short.auth_policy


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("api_key_header_name", "", "HEADER"),
        ("min_api_key_length", 8, "MIN_API_KEY_LENGTH"),
        ("idempotency_ttl_hours", 0, "TTL_HOURS"),
        ("idempotency_processing_timeout_sec", 0, "PROCESSING_TIMEOUT"),
        ("idempotency_max_key_length", 8, "MAX_KEY_LENGTH"),
        ("storage_scan_cache_ttl_sec", -1, "STORAGE_SCAN"),
        ("conversation_stats_cache_ttl_sec", -1, "CONVERSATION_STATS"),
    ],
)
def test_validate_runtime_rejects_invalid_operational_settings(field: str, value, message: str):
    kwargs = {
        "environment": "test",
        "auth_mode": "required",
        "api_key": "x" * 32,
        "legacy_api_compatibility_mode": "disabled",
        "legacy_api_phase": "sunset",
    }
    kwargs[field] = value
    cfg = Settings(**kwargs)

    with pytest.raises(ValueError, match=message):
        cfg.validate_runtime()


def test_validate_runtime_rejects_processing_timeout_longer_than_ttl_and_bad_legacy_phase():
    timeout_cfg = Settings(
        environment="test",
        auth_mode="required",
        api_key="x" * 32,
        idempotency_ttl_hours=1,
        idempotency_processing_timeout_sec=3601,
        legacy_api_compatibility_mode="disabled",
        legacy_api_phase="sunset",
    )
    with pytest.raises(ValueError, match="ttl window"):
        timeout_cfg.validate_runtime()

    legacy_cfg = Settings(
        environment="test",
        auth_mode="required",
        api_key="x" * 32,
        legacy_api_compatibility_mode="disabled",
        legacy_api_phase="warning",
    )
    with pytest.raises(ValueError, match="phase must be sunset"):
        legacy_cfg.validate_runtime()
