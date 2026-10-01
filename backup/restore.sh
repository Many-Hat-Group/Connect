#!/bin/sh
# Restore a pg_dump into the running postgres. DESTRUCTIVE: replaces the
# current database contents. Usage (from the repo root):
#   docker compose exec backup sh /opt/backup/restore.sh /backups/connect-YYYYMMDDTHHMMSSZ.dump
#   docker compose exec backup sh /opt/backup/restore.sh /backups/... --into connect_restore_test
set -eu
f=${1:?dump file}
db=${PGDATABASE:-connect}
if [ "${2:-}" = "--into" ]; then
  db=${3:?database name}
  createdb "$db" 2>/dev/null || true
fi
pg_restore --clean --if-exists --no-owner --dbname="$db" "$f"
echo "restore: $f restored into $db"
