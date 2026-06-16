"""工具系统的基础抽象与结果类型定义。"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

from pdf_agent.schemas.tool import ToolManifest


class ProgressReporter(Protocol):
    """工具运行过程中用于上报进度的回调协议。"""
    def __call__(self, percent: int, message: str = "") -> None: ...


@dataclass
class ToolResult:
    """工具 `run()` 方法返回的统一结果结构。"""
    output_files: list[Path] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    log: str = ""


class BaseTool(abc.ABC):
    """所有 PDF 工具都要继承的抽象基类。"""

    @abc.abstractmethod
    def manifest(self) -> ToolManifest:
        """返回描述工具能力的 manifest。"""

    @abc.abstractmethod
    def validate(self, params: dict) -> dict:
        """校验并规范化参数；输入非法时应抛出 `ToolError`。"""
        ...

    @abc.abstractmethod
    def run(
        self,
        inputs: list[Path],
        params: dict,
        workdir: Path,
        reporter: ProgressReporter | None = None,
    ) -> ToolResult:
        """执行工具主体逻辑并返回结果。"""
        ...

    @cached_property
    def _cached_name(self) -> str:
        return self.manifest().name

    @property
    def name(self) -> str:
        return self._cached_name
