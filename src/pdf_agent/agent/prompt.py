"""System prompt builder for the LangGraph agent."""
from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from pdf_agent.agent.state import FileInfo
from pdf_agent.i18n import get_system_prompt


def build_system_prompt(files: list[FileInfo], current_files: list[str]) -> str:
    """Build the full system prompt with dynamic file context."""
    parts = [get_system_prompt()]

    if files:
        parts.append("\n## Files in this conversation\n")
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


def prepare_messages_for_model(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Inject selected input context and normalized hints into human messages for model consumption only."""
    prepared: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
            selected_inputs = additional_kwargs.get("selected_inputs")
            hints = additional_kwargs.get("normalized_intent_hints")
            content_parts: list[str] = [message.content] if isinstance(message.content, str) else [str(message.content)]
            if isinstance(selected_inputs, list) and selected_inputs:
                lines = ["[Selected input files for this turn]"]
                for item in selected_inputs:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "") or "").strip()
                    if not name:
                        continue
                    source = str(item.get("source", "") or "").strip() or "unknown"
                    lines.append(f"- {name} (source: {source})")
                if len(lines) > 1:
                    lines.append("Treat these files as already selected and available now. Do not ask the user to re-upload or re-select them unless the tool actually fails to open them.")
                    content_parts.append("\n".join(lines))
            if isinstance(hints, str) and hints.strip():
                content_parts.append(f"[Normalized intent hints]\n{hints.strip()}")
            if len(content_parts) > 1:
                prepared.append(HumanMessage(
                    content="\n\n".join(part for part in content_parts if part),
                    additional_kwargs=additional_kwargs,
                ))
                continue
        prepared.append(message)
    return prepared
