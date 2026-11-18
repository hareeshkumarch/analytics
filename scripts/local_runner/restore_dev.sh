#!/usr/bin/env bash
# Restore a backup made by backup_dev.sh into the running compose stack.
# Usage: scripts/local_runner/restore_dev.sh <backup_dir>
set -euo pipefail
cd "$(dirname "$0")/../.."

BACKUP_DIR="${1:?Usage: restore_dev.sh <backup_dir>}"
test -f "$BACKUP_DIR/db.dump" || { echo "Missing $BACKUP_DIR/db.dump" >&2; exit 1; }

# --clean --if-exists replaces existing objects; --single-transaction rolls back on any error.
docker compose exec -T postgres pg_restore -U postgres -d analytics_poc \
  --clean --if-exists --single-transaction --no-owner < "$BACKUP_DIR/db.dump"

if [ -f "$BACKUP_DIR/artifacts.tar.gz" ]; then
  docker compose exec -T api tar -xzf - -C /data/artifacts < "$BACKUP_DIR/artifacts.tar.gz"
fi

echo "Restored from $BACKUP_DIR. Restart the api service so it reloads state: docker compose restart api"
