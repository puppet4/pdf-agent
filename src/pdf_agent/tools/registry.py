"""Tool Registry - discovers and manages tool plugins."""
from __future__ import annotations

import logging

from pdf_agent.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry for all available PDF tools."""

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


# Global registry instance
registry = ToolRegistry()


def load_builtin_tools() -> None:
    """Load all built-in tools into the global registry."""
    from pdf_agent.tools._builtins import get_builtin_tools

    for tool in get_builtin_tools():
        registry.register(tool)
