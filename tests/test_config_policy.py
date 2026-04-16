from __future__ import annotations

import pytest

from pdf_agent.config import Settings


def test_default_auth_policy_is_secure_by_default():
    cfg = Settings(environment="development", auth_mode="required", api_key="dev-local-api-key")
    policy = cfg.auth_policy

    assert policy.enabled is True
    assert policy.mode == "required"
    assert policy.api_key == "dev-local-api-key"


def test_production_rejects_weak_default_api_key():
    cfg = Settings(environment="production", auth_mode="required", api_key="dev-local-api-key")

    with pytest.raises(ValueError, match="weak/default"):
        _ = cfg.auth_policy


def test_production_rejects_disabled_auth_mode():
    cfg = Settings(environment="production", auth_mode="disabled", api_key="strong-key")

    with pytest.raises(ValueError, match="not allowed"):
        _ = cfg.auth_policy


def test_optional_auth_can_be_disabled_only_when_key_missing_in_non_production():
    cfg = Settings(environment="development", auth_mode="optional", api_key="")
    policy = cfg.auth_policy

    assert policy.enabled is False
    assert policy.reason == "optional mode without API key"
