"""Add users and thread_ownership tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users table
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(256) NOT NULL,
            storage_quota_mb INTEGER NOT NULL DEFAULT 1024,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # thread_ownership table
    op.execute("""
        CREATE TABLE IF NOT EXISTS thread_ownership (
            thread_id VARCHAR(64) PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_thread_ownership_user ON thread_ownership(user_id)")

    # Add optional user_id column to files (nullable for backwards compat)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE files ADD COLUMN user_id UUID;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS thread_ownership")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE files DROP COLUMN user_id;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$;
    """)
