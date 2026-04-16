# Migration Rollback Runbook

## Scope

This runbook covers schema migrations from:

- `0001` -> base `files` table
- `0002` -> `idempotency_records`
- `0003` -> `files.idempotency_key_hash`

`0002/0003` contain reliability metadata only. Rolling back these revisions may lose idempotency cache state, but does not delete file binaries already stored on disk.

## Pre-check

1. Confirm DB connectivity and credentials.
2. Confirm application is in maintenance window (or read-only mode for write APIs).
3. Backup database (or ensure PITR snapshot exists).

## Upgrade Procedure

1. Run:
   ```bash
   alembic upgrade head
   ```
2. Validate:
   - Table `idempotency_records` exists.
   - Column `files.idempotency_key_hash` exists.
3. Run smoke checks:
   - `GET /healthz`
   - upload API + idempotency replay check

## Rollback Procedure

1. Roll back to baseline revision:
   ```bash
   alembic downgrade 0001
   ```
2. Validate:
   - `idempotency_records` is absent.
   - `files.idempotency_key_hash` is absent.
3. Restart application to clear stale runtime assumptions.

## Automated Verification

Use:

```bash
bash scripts/verify_migrations.sh
```

This script executes:

1. `upgrade head`
2. schema assertions
3. `downgrade 0001`
4. schema assertions
5. `upgrade head`

CI should fail-fast if any step fails.
