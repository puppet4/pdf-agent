"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-17 18:02:08.610915
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'files',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('orig_name', sa.String(512), nullable=False),
        sa.Column('mime_type', sa.String(128), nullable=False),
        sa.Column('size_bytes', sa.Integer, nullable=False),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('page_count', sa.Integer, nullable=True),
        sa.Column('storage_path', sa.String(1024), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_files_sha256', 'files', ['sha256'])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_files_sha256")
    op.execute("DROP TABLE IF EXISTS files CASCADE")
