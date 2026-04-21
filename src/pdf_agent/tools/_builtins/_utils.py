"""Shared utilities for builtin tools."""
from __future__ import annotations


def to_bool(value: object) -> bool:
    """Convert a loosely-typed value to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
