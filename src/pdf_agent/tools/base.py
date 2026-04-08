"""Tool plugin system - base classes and result types."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

from pdf_agent.schemas.tool import ToolManifest


class ProgressReporter(Protocol):
    """Callback for reporting progress within a tool run."""
    def __call__(self, percent: int, message: str = "") -> None: ...


@dataclass
class ToolResult:
    """Result returned by a tool's run method."""
    output_files: list[Path] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    log: str = ""


class BaseTool(abc.ABC):
    """Abstract base class for all PDF tools."""

    @abc.abstractmethod
    def manifest(self) -> ToolManifest:
        """Return the tool's manifest describing its capabilities."""

    @abc.abstractmethod
    def validate(self, params: dict) -> dict:
        """Validate and normalize parameters. Raise ToolError on invalid input."""
        ...

    @abc.abstractmethod
    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        """Run the tool and return results."""
        ...

    @cached_property
    def _cached_name(self) -> str:
        return self.manifest().name

    @property
    def name(self) -> str:
        return self._cached_name
