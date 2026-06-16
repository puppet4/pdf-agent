"""定义 LangGraph agent 使用的状态结构。"""
from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class FileInfo(TypedDict):
    file_id: str
    path: str
    orig_name: str
    mime_type: str
    page_count: int | None
    # 取值可能是 `upload`，也可能是生成该文件的工具名。
    source: str
    # 当来源是会话产物时，记录类似 `step_1/output.pdf` 的相对路径。
    artifact_path: NotRequired[str]


def files_reducer(existing: list[FileInfo], new: list[FileInfo]) -> list[FileInfo]:
    """用于文件列表的追加型 reducer，并按路径去重。"""
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
    # 当前激活的最新输出文件路径列表，会直接覆盖旧值。
    current_files: list[str]
    conversation_workdir: str
    step_counter: int
