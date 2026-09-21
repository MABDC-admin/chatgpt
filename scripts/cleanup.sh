#!/usr/bin/env bash
# Reclaim disk from chat attachments whose rows have aged out.
#
# Usage:   ./scripts/cleanup.sh [--dry-run]
# Crontab: 30 3 * * * /opt/teacherai-school/scripts/cleanup.sh >> /var/log/teacherai-cleanup.log 2>&1
#
# Attachments are working files: a teacher uploads a PDF, asks about it, and
# never refers to it again. The extracted text is already inlined into the
# stored message, so the original stops being needed once the turn is over.
# Generated images and knowledge-base documents are NOT touched here -- those
# are user-visible content with their own delete flows.

set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/teacherai-school}"
KEEP_DAYS="${KEEP_DAYS:-30}"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN="yes"

cd "$COMPOSE_DIR"

echo "[$(date)] Attachment cleanup, retention ${KEEP_DAYS} days${DRY_RUN:+ (DRY RUN)}"

BEFORE=$(docker compose exec -T backend sh -c 'du -sm /data/uploads 2>/dev/null | cut -f1' || echo 0)

# Delete the rows first and have Postgres hand back the paths it removed, so a
# file is only ever unlinked when its row is definitely gone. Doing it the
# other way round risks rows pointing at files that no longer exist.
SQL="DELETE FROM attachments WHERE created_at < now() - interval '${KEEP_DAYS} days' RETURNING storage_path;"
[ -n "$DRY_RUN" ] && SQL="SELECT storage_path FROM attachments WHERE created_at < now() - interval '${KEEP_DAYS} days';"

PATHS=$(docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-teacherai}" -d "${POSTGRES_DB:-teacherai}" \
  -t -A -c "$SQL" | grep -v '^$' || true)

COUNT=0
if [ -n "$PATHS" ]; then
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    COUNT=$((COUNT + 1))
    if [ -z "$DRY_RUN" ]; then
      docker compose exec -T backend sh -c "rm -f '$p'" || true
    fi
  done <<< "$PATHS"
fi

AFTER=$(docker compose exec -T backend sh -c 'du -sm /data/uploads 2>/dev/null | cut -f1' || echo 0)
echo "[$(date)] ${COUNT} attachment(s) ${DRY_RUN:+would be }removed; /data/uploads ${BEFORE}MB -> ${AFTER}MB"

# Warn before the volume becomes a problem rather than after.
USED=$(docker compose exec -T backend sh -c 'df -P /data | tail -1 | tr -s " " | cut -d" " -f5' | tr -d '%')
[ "${USED:-0}" -ge 80 ] && echo "[$(date)] WARNING: /data is ${USED}% full"
exit 0
