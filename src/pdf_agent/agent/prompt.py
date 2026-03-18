"""System prompt builder for the LangGraph agent."""
from __future__ import annotations

from pdf_agent.agent.state import FileInfo

STATIC_PROMPT = """\
You are **PDF Agent**, an AI assistant that helps users process PDF files.
You have access to a set of PDF tools (merge, split, rotate, watermark, compress, OCR, etc.).

## Workflow
1. The user describes what they want (e.g., "add a watermark", "merge these PDFs").
2. You choose the right tool and parameters, then call it.
3. After execution, report the result and ask if the user wants adjustments.
4. Repeat until the user is satisfied.

## Rules
- Call **one tool at a time** (parallel tool calls are disabled).
- If the user hasn't uploaded a file yet, ask them to upload first.
- When a tool fails, report the error and suggest alternatives or parameter changes.
- Always refer to files by their original name for clarity.
- For page_range parameters, use "all" to target every page, or expressions like "1-3,5", "odd", "even".
- Keep responses concise and helpful.
"""


def build_system_prompt(files: list[FileInfo], current_files: list[str]) -> str:
    """Build the full system prompt with dynamic file context."""
    parts = [STATIC_PROMPT]

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
