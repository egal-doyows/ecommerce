#!/bin/bash
# backup.sh — daily backup of database + media.
# Run via cron as the `deploy` user. Reads POSTGRES_PASSWORD from .env.

set -euo pipefail

BACKUP_DIR=/home/deploy/backups
APP_DIR=/home/deploy/ecommerce
KEEP_DAYS=30
TIMESTAMP=$(date +%F-%H%M)

mkdir -p "$BACKUP_DIR"

# Source the app's .env so POSTGRES_PASSWORD is available.
set -a
# shellcheck disable=SC1091
source "$APP_DIR/.env"
set +a

# Postgres dump.
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    -h localhost \
    -U "${POSTGRES_USER:-ecommerce}" \
    "${POSTGRES_DB:-ecommerce}" \
    | gzip > "$BACKUP_DIR/db-$TIMESTAMP.sql.gz"

# Media tarball.
tar -czf "$BACKUP_DIR/media-$TIMESTAMP.tar.gz" -C "$APP_DIR" static/media

# Prune old.
find "$BACKUP_DIR" -name "*.gz" -mtime +"$KEEP_DAYS" -delete

echo "[$TIMESTAMP] Backup complete." >> "$BACKUP_DIR/backup.log"
