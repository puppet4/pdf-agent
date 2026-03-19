#!/usr/bin/env bash
# PDF Agent backup script
# Usage: ./scripts/backup.sh [backup_dir]
# Cron (daily at 2am): 0 2 * * * /app/scripts/backup.sh /backups >> /var/log/pdf-agent-backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${1:-/tmp/pdf-agent-backups}"
DATE=$(date +%Y%m%d_%H%M%S)
DEST="${BACKUP_DIR}/${DATE}"
KEEP_DAYS="${KEEP_DAYS:-7}"

DB_URL="${PDF_AGENT_CHECKPOINTER_DB_URL:-postgresql://postgres:postgres@localhost:5432/pdf_agent}"
DATA_DIR="${PDF_AGENT_DATA_DIR:-data}"

mkdir -p "${DEST}"
echo "[$(date)] Starting backup to ${DEST}"

# 1. PostgreSQL dump
echo "[$(date)] Dumping PostgreSQL..."
DB_USER=$(echo "${DB_URL}" | sed -E 's|.*://([^:]+):.*|\1|')
DB_PASS=$(echo "${DB_URL}" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
DB_HOST=$(echo "${DB_URL}" | sed -E 's|.*@([^:/]+)[:/].*|\1|')
DB_PORT=$(echo "${DB_URL}" | sed -E 's|.*:([0-9]+)/.*|\1|')
DB_NAME=$(echo "${DB_URL}" | sed -E 's|.*/([^?]+).*|\1|')

PGPASSWORD="${DB_PASS}" pg_dump \
    -h "${DB_HOST}" \
    -p "${DB_PORT}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    -F custom \
    -f "${DEST}/database.pgdump"

echo "[$(date)] Database dump: $(du -sh ${DEST}/database.pgdump | cut -f1)"

# 2. Data directory
if [ -d "${DATA_DIR}" ]; then
    echo "[$(date)] Archiving data directory..."
    tar -czf "${DEST}/data.tar.gz" -C "$(dirname ${DATA_DIR})" "$(basename ${DATA_DIR})"
    echo "[$(date)] Data archive: $(du -sh ${DEST}/data.tar.gz | cut -f1)"
else
    echo "[$(date)] Warning: data dir '${DATA_DIR}' not found, skipping"
fi

# 3. Manifest
cat > "${DEST}/manifest.txt" << EOF
PDF Agent Backup
Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Database: ${DB_NAME}@${DB_HOST}:${DB_PORT}
Data dir: ${DATA_DIR}
EOF

echo "[$(date)] Backup complete: ${DEST}"

# 4. Cleanup old backups
find "${BACKUP_DIR}" -maxdepth 1 -type d -mtime +"${KEEP_DAYS}" -exec rm -rf {} + 2>/dev/null || true
echo "[$(date)] Done."
