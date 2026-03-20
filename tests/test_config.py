"""Tests for config surface consistency."""
from __future__ import annotations

from pathlib import Path


class TestSettings:
    def test_jwt_settings_removed(self):
        from pdf_agent.config import settings

        assert not hasattr(settings, "jwt_secret")
        assert not hasattr(settings, "jwt_algorithm")
        assert not hasattr(settings, "jwt_expire_hours")

    def test_checkpointer_db_url_removed(self):
        from pdf_agent.config import settings

        assert not hasattr(settings, "checkpointer_db_url")

    def test_local_env_does_not_embed_live_openai_key(self):
        env_text = Path(".env").read_text()

        assert "PDF_AGENT_OPENAI_API_KEY=sk-" not in env_text
