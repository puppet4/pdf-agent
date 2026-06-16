"""页码范围解析器。

支持的语法包括：
  - `all`
  - `1-3,5,7-9`
  - `odd` / `even`
  - `last`、`last-2-last` 这类尾页表达式

输入页码从 1 开始，返回值会转换成从 0 开始的索引。
"""
from __future__ import annotations

import re

from pdf_agent.core import ErrorCode, PDFAgentError


def parse_page_range(expr: str, total_pages: int) -> list[int]:
    """解析页码范围表达式，并返回从 0 开始的页索引列表。"""
    expr = expr.strip().lower()
    if not expr:
        raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, "Empty page range expression")

    if expr == "all":
        return list(range(total_pages))
    if expr == "odd":
        return list(range(0, total_pages, 2))
    if expr == "even":
        return list(range(1, total_pages, 2))

    pages: list[int] = []
    parts = expr.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part = _resolve_last(part, total_pages)
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            _validate_bounds(start, end, total_pages)
            pages.extend(range(start - 1, end))
        elif re.fullmatch(r"\d+", part):
            pg = int(part)
            _validate_bounds(pg, pg, total_pages)
            pages.append(pg - 1)
        else:
            raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, f"Invalid page range token: '{part}'")
    return list(dict.fromkeys(pages))


def _resolve_last(expr: str, total_pages: int) -> str:
    """把 `last` 关键字展开成具体页码，例如 `last` 或 `last-N`。"""
    def _replace(m: re.Match[str]) -> str:
        offset = m.group(1)
        if offset:
            return str(total_pages - int(offset))
        return str(total_pages)

    return re.sub(r"last(?:-(\d+))?", _replace, expr)


def _validate_bounds(start: int, end: int, total_pages: int) -> None:
    if start < 1 or end < 1 or start > total_pages or end > total_pages:
        raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, f"Page out of range: {start}-{end} (total={total_pages})")
    if start > end:
        raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, f"Invalid range: {start} > {end}")
