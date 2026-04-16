#!/usr/bin/env bash
set -euo pipefail

echo "[migrate] upgrade -> head"
alembic upgrade head

python - <<'PY'
import os
from sqlalchemy import create_engine, text

db_url = os.environ.get("PDF_AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not db_url:
    raise SystemExit("PDF_AGENT_DATABASE_URL (or DATABASE_URL) is required")
sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
engine = create_engine(sync_url)
with engine.connect() as conn:
    tables = {row[0] for row in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert "files" in tables, "files table missing after upgrade"
    assert "idempotency_records" in tables, "idempotency_records table missing after upgrade"
    cols = {row[0] for row in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='files'"
    ))}
    assert "idempotency_key_hash" in cols, "files.idempotency_key_hash missing after upgrade"
print("upgrade verification passed")
PY

echo "[migrate] downgrade -> 0001"
alembic downgrade 0001

python - <<'PY'
import os
from sqlalchemy import create_engine, text

db_url = os.environ.get("PDF_AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not db_url:
    raise SystemExit("PDF_AGENT_DATABASE_URL (or DATABASE_URL) is required")
sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
engine = create_engine(sync_url)
with engine.connect() as conn:
    tables = {row[0] for row in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert "idempotency_records" not in tables, "idempotency_records should not exist after downgrade to 0001"
    cols = {row[0] for row in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='files'"
    ))}
    assert "idempotency_key_hash" not in cols, "files.idempotency_key_hash should not exist after downgrade to 0001"
print("downgrade verification passed")
PY

echo "[migrate] re-upgrade -> head"
alembic upgrade head
echo "migration verification passed"
