"""Built-in tools package."""
from __future__ import annotations

from pdf_agent.tools.base import BaseTool


def get_builtin_tools() -> list[BaseTool]:
    """Return all built-in tool instances."""
    from pdf_agent.tools._builtins.merge import MergeTool
    from pdf_agent.tools._builtins.split import SplitTool
    from pdf_agent.tools._builtins.rotate import RotateTool
    from pdf_agent.tools._builtins.metadata_info import MetadataInfoTool

    return [
        MergeTool(),
        SplitTool(),
        RotateTool(),
        MetadataInfoTool(),
    ]
