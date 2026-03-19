"""System prompt builder for the LangGraph agent."""
from __future__ import annotations

from pdf_agent.agent.state import FileInfo
from pdf_agent.i18n import get_system_prompt


def build_system_prompt(files: list[FileInfo], current_files: list[str]) -> str:
    """Build the full system prompt with dynamic file context."""
    parts = [get_system_prompt()]

    if files:
        parts.append("\n## Files in this session\n")
        parts.append("| # | Name | Type | Pages | Source | Path |")
        parts.append("|---|------|------|-------|--------|------|")
        for i, f in enumerate(files, 1):
            pages = str(f.get("page_count") or "—")
            parts.append(
                f"| {i} | {f['orig_name']} | {f['mime_type']} | {pages} | {f['source']} | `{f['path']}` |"
            )

    if current_files:
        parts.append("\n## Current active files (default input for next tool)")
        for p in current_files:
            parts.append(f"- `{p}`")
    else:
        parts.append("\n*No active files yet. The user needs to upload a file first.*")

    return "\n".join(parts)
