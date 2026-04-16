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
