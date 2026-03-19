"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-17 18:02:08.610915
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums (idempotent)
    op.execute("""
        DO $$ BEGIN CREATE TYPE jobstatus AS ENUM ('PENDING','RUNNING','SUCCESS','FAILED','CANCELED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE jobmode AS ENUM ('FORM','AGENT');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE stepstatus AS ENUM ('PENDING','RUNNING','SUCCESS','FAILED','SKIPPED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE artifacttype AS ENUM ('input','intermediate','output');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)

    # files
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

    # jobs (uses TEXT columns to avoid sa.Enum auto-DDL)
    op.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id UUID PRIMARY KEY,
            status jobstatus NOT NULL,
            mode jobmode NOT NULL,
            instruction TEXT,
            plan_json JSON NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            error_code VARCHAR(64),
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            result_path VARCHAR(1024),
            result_type VARCHAR(32)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status_created ON jobs(status, created_at)")

    # job_steps
    op.execute("""
        CREATE TABLE IF NOT EXISTS job_steps (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES jobs(id),
            idx INTEGER NOT NULL,
            tool_name VARCHAR(128) NOT NULL,
            params_json JSON NOT NULL,
            status stepstatus NOT NULL,
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            log_text TEXT,
            output_path VARCHAR(1024)
        )
    """)

    # artifacts
    op.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES jobs(id),
            step_id UUID REFERENCES job_steps(id),
            type artifacttype NOT NULL,
            path VARCHAR(1024) NOT NULL,
            meta_json JSON
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifacts CASCADE")
    op.execute("DROP TABLE IF EXISTS job_steps CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_jobs_status_created")
    op.execute("DROP TABLE IF EXISTS jobs CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_files_sha256")
    op.execute("DROP TABLE IF EXISTS files CASCADE")
    op.execute("DROP TYPE IF EXISTS artifacttype")
    op.execute("DROP TYPE IF EXISTS stepstatus")
    op.execute("DROP TYPE IF EXISTS jobmode")
    op.execute("DROP TYPE IF EXISTS jobstatus")
