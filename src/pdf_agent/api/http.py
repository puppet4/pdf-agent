"""Shared HTTP helpers for API responses."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def content_disposition_headers(filename: str, *, inline: bool) -> dict[str, str]:
    """Build RFC 6266/RFC 5987 compatible Content-Disposition headers."""
    disposition = "inline" if inline else "attachment"
    safe_name = filename.replace("\\", "_").replace("\r", "").replace("\n", "").replace('"', "")
    ascii_fallback = safe_name.encode("ascii", "ignore").decode("ascii").strip(" .")
    if not ascii_fallback:
        suffix = Path(safe_name).suffix.encode("ascii", "ignore").decode("ascii")
        ascii_fallback = f"download{suffix}" if suffix else "download"
    encoded_name = quote(safe_name, safe="")
    return {
        "Content-Disposition": (
            f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded_name}'
        )
    }
