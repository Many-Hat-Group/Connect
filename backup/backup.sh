#!/bin/sh
# Nightly pg_dump with 14-day retention. "backup.sh once" runs a single dump.
set -eu
: "${PGHOST:=postgres}" "${PGUSER:=connect}" "${PGDATABASE:=connect}"
: "${BACKUP_DIR:=/backups}" "${BACKUP_KEEP_DAYS:=14}" "${BACKUP_AT:=02:30}"
export PGHOST PGUSER PGDATABASE

dump() {
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  f="$BACKUP_DIR/connect-$ts.dump"
  pg_dump --format=custom --compress=6 --file="$f.part" && mv "$f.part" "$f"
  echo "backup: wrote $f ($(du -h "$f" | cut -f1))"
  find "$BACKUP_DIR" -name 'connect-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -print -delete
}

if [ "${1:-}" = "once" ]; then dump; exit 0; fi

while :; do
  now=$(date -u +%s)
  target=$(date -u -d "$(date -u +%Y-%m-%d) $BACKUP_AT" +%s 2>/dev/null || date -u -D '%Y-%m-%d %H:%M' -d "$(date -u +%Y-%m-%d) $BACKUP_AT" +%s)
  [ "$target" -le "$now" ] && target=$((target + 86400))
  sleep $((target - now))
  dump || echo "backup: pg_dump FAILED"
done
