"""Tests for repository helper scripts."""
from __future__ import annotations

from pathlib import Path


class TestScripts:
    def test_e2e_script_counts_tools_from_tools_array(self):
        content = Path("scripts/test_e2e.sh").read_text()

        assert "['tools']" in content
        assert "len(json.load(sys.stdin))" not in content
