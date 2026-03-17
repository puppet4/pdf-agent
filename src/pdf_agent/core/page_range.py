"""Page range parser.

Syntax:
  - "all"
  - "1-3,5,7-9"
  - "odd" / "even"
  - Supports "last", "last-2-last"

All input is 1-based; output is 0-based indices.
"""
from __future__ import annotations

import re

from pdf_agent.core import ErrorCode, PDFAgentError


def parse_page_range(expr: str, total_pages: int) -> list[int]:
    """Parse a page range expression and return 0-based page indices."""
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
    return pages


def _resolve_last(expr: str, total_pages: int) -> str:
    """Resolve 'last' keyword: 'last' -> total, 'last-N' -> total-N+1."""
    import re as _re

    def _replace(m: _re.Match) -> str:
        offset = m.group(1)
        if offset:
            return str(total_pages - int(offset))
        return str(total_pages)

    return _re.sub(r"last(?:-(\d+))?", _replace, expr)


def _validate_bounds(start: int, end: int, total_pages: int) -> None:
    if start < 1 or end < 1 or start > total_pages or end > total_pages:
        raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, f"Page out of range: {start}-{end} (total={total_pages})")
    if start > end:
        raise PDFAgentError(ErrorCode.INVALID_PAGE_RANGE, f"Invalid range: {start} > {end}")
