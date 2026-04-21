"""File-related schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    id: uuid.UUID
    orig_name: str
    mime_type: str
    size_bytes: int
    page_count: int | None = None
    created_at: datetime
    download_url: str = ""
    thumbnail_url: str | None = None
