from __future__ import annotations

import pytest

from pdf_agent.services.idempotency import build_request_hash, hash_idempotency_key, normalize_idempotency_key


def test_build_request_hash_is_stable_across_dict_key_order():
    payload_a = {"b": 2, "a": 1, "nested": {"z": 9, "x": 1}}
    payload_b = {"a": 1, "nested": {"x": 1, "z": 9}, "b": 2}

    assert build_request_hash(payload_a) == build_request_hash(payload_b)


def test_normalize_idempotency_key_rejects_oversized_values(monkeypatch: pytest.MonkeyPatch):
    from pdf_agent.config import settings

    monkeypatch.setattr(settings, "idempotency_max_key_length", 8)

    with pytest.raises(ValueError, match="too long"):
        normalize_idempotency_key("123456789")


def test_normalize_idempotency_key_trims_blank_values_to_none():
    assert normalize_idempotency_key("   ") is None
    assert normalize_idempotency_key(None) is None
    assert normalize_idempotency_key("  key-1  ") == "key-1"


def test_hash_idempotency_key_normalizes_input():
    assert hash_idempotency_key("key-1") == hash_idempotency_key("  key-1  ")
