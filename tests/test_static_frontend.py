"""Tests for static frontend defaults."""
from __future__ import annotations

from pathlib import Path


class TestStaticFrontend:
    def test_files_tab_is_default_active(self):
        html = Path("src/pdf_agent/static/index.html").read_text()
        assert '<button class="sidebar-tab" onclick="switchTab(\'chat\',this)">' in html
        assert '<button class="sidebar-tab active" onclick="switchTab(\'files\',this)">' in html
        assert '<div class="tab-panel" id="tab-chat">' in html
        assert '<div class="tab-panel active" id="tab-files">' in html

    def test_app_title_uses_toolbox_wording(self):
        html = Path("src/pdf_agent/static/index.html").read_text()
        assert "<title>PDF Toolbox</title>" in html
        assert '<h2 id="appTitle">PDF Toolbox</h2>' in html

    def test_frontend_checks_capabilities(self):
        js = Path("src/pdf_agent/static/app.js").read_text()
        assert "loadCapabilities()" in js
        assert "/healthz" in js
        assert "_capabilities = { agent: false, workflows: false }" in js

    def test_tools_page_uses_toolbox_wording(self):
        html = Path("src/pdf_agent/static/tools.html").read_text()
        assert "<title>PDF Toolbox" in html
        assert "PDF Toolbox" in html
        assert "through the AI Agent" not in html


class TestI18nStrings:
    def test_i18n_strings_use_toolbox_wording(self):
        from pdf_agent.i18n import PROMPTS, UI_STRINGS

        assert "PDF Agent" not in UI_STRINGS["en"]["app_title"]
        assert "PDF Agent" not in UI_STRINGS["en"]["empty_title"]
        assert "PDF Agent" not in UI_STRINGS["zh"]["empty_title"]
        assert "PDF Agent" not in PROMPTS["en"]
