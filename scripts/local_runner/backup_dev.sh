#!/usr/bin/env bash
# Back up the dev database and snapshot artifacts from the running compose stack.
# Usage: scripts/local_runner/backup_dev.sh [backup_dir]
set -euo pipefail
cd "$(dirname "$0")/../.."

BACKUP_DIR="${1:-./backups/$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$BACKUP_DIR"

# Custom-format dump so restore can use pg_restore --clean in one transaction.
docker compose exec -T postgres pg_dump -U postgres -Fc analytics_poc > "$BACKUP_DIR/db.dump"

# Artifacts live in the named volume mounted at /data/artifacts inside the api container.
docker compose exec -T api tar -czf - -C /data/artifacts . > "$BACKUP_DIR/artifacts.tar.gz"

echo "Backup created at $BACKUP_DIR"
