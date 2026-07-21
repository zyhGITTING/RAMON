#!/bin/sh
set -eu

# Manual, destructive restore is deliberately gated.  The default target is a
# dedicated verification database, never the production database.

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGUSER:=datamid_owner}"
: "${DATAMID_DB_ADMIN_PASSWORD:?DATAMID_DB_ADMIN_PASSWORD is required}"
: "${RESTORE_TARGET_DATABASE:=datamid_restore_verify}"

dump_file="${1:-}"
if [ -z "$dump_file" ] || [ ! -f "$dump_file" ]; then
  echo "Usage: postgres_restore_verify.sh /backups/logical/<backup>.dump" >&2
  exit 2
fi

case "$RESTORE_TARGET_DATABASE" in
  datamid|postgres|template0|template1|"")
    echo "RESTORE_TARGET_DATABASE must be a dedicated non-production database" >&2
    exit 2
    ;;
esac

export PGHOST PGPORT PGUSER
export PGPASSWORD="$DATAMID_DB_ADMIN_PASSWORD"

checksum_file="${dump_file}.sha256"
if [ -f "$checksum_file" ]; then
  (cd "$(dirname "$dump_file")" && sha256sum -c "$(basename "$checksum_file")")
fi
pg_restore --list "$dump_file" >/dev/null

dropdb --if-exists --force --maintenance-db=postgres "$RESTORE_TARGET_DATABASE"
createdb --maintenance-db=postgres "$RESTORE_TARGET_DATABASE"

cleanup() {
  dropdb --if-exists --force --maintenance-db=postgres "$RESTORE_TARGET_DATABASE" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

pg_restore \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  --dbname="$RESTORE_TARGET_DATABASE" \
  "$dump_file"

psql --dbname="$RESTORE_TARGET_DATABASE" --no-psqlrc --set=ON_ERROR_STOP=1 <<'SQL'
SELECT current_database() AS restored_database;
SELECT count(*) AS application_tables
FROM information_schema.tables
WHERE table_schema = 'public';
SELECT count(*) AS users FROM sys_user;
SELECT count(*) AS datasources FROM sys_datasource;
SQL

echo "Restore verification passed for $dump_file"

