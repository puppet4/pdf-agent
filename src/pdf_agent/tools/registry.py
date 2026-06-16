"""工具注册表，负责发现并管理可用工具。"""
from __future__ import annotations

import logging

from pdf_agent.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """全部 PDF 工具的中心注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        name = tool.name
        if name in self._tools:
            logger.warning("Tool '%s' already registered, overwriting", name)
        self._tools[name] = tool
        logger.info("Registered tool: %s", name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def list_manifests(self) -> list[dict]:
        return [t.manifest().model_dump() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


# 全局共享的注册表实例
registry = ToolRegistry()


def load_builtin_tools() -> None:
    """把全部内建工具加载到全局注册表中。"""
    from pdf_agent.tools._builtins import get_builtin_tools

    for tool in get_builtin_tools():
        registry.register(tool)
