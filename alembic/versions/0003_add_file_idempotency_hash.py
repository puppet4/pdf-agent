"""add file idempotency key hash

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-16 16:40:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("files", sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_files_idempotency_key_hash", "files", ["idempotency_key_hash"])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_files_idempotency_key_hash")
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS idempotency_key_hash")
