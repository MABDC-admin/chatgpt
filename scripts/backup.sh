#!/usr/bin/env bash
# Daily PostgreSQL backup for the Teacher AI platform.
#
# Usage:   ./scripts/backup.sh
# Crontab: 0 2 * * * /opt/teacherai-school/scripts/backup.sh >> /var/log/teacherai-backup.log 2>&1
#
# Keeps the last 14 daily backups; older ones are pruned automatically.

set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/teacherai-school}"
BACKUP_DIR="${BACKUP_DIR:-/opt/teacherai-backups}"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="$BACKUP_DIR/teacherai_${TIMESTAMP}.sql.gz"

echo "[$(date)] Starting backup → $DUMP_FILE"

docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-teacherai}" "${POSTGRES_DB:-teacherai}" \
  --clean --if-exists --no-owner \
  | gzip > "$DUMP_FILE"

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "[$(date)] Backup complete: $SIZE"

# Prune old backups
find "$BACKUP_DIR" -name "teacherai_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
REMAINING=$(find "$BACKUP_DIR" -name "teacherai_*.sql.gz" | wc -l)
echo "[$(date)] $REMAINING backup(s) on disk after pruning"
