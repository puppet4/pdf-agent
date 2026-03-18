"""Graph state definitions for the LangGraph agent."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class FileInfo(TypedDict):
    file_id: str
    path: str
    orig_name: str
    mime_type: str
    page_count: int | None
    source: str  # "upload" | tool name that produced it


def files_reducer(existing: list[FileInfo], new: list[FileInfo]) -> list[FileInfo]:
    """Append-only reducer for the files list. Deduplicates by path."""
    seen = {f["path"] for f in existing}
    result = list(existing)
    for f in new:
        if f["path"] not in seen:
            result.append(f)
            seen.add(f["path"])
    return result


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    files: Annotated[list[FileInfo], files_reducer]
    current_files: list[str]  # latest output file paths (replace reducer)
    thread_workdir: str
    step_counter: int
