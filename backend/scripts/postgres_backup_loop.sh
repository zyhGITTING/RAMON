#!/bin/sh
set -eu

# Runs inside the postgres:16-alpine backup sidecar.  The backup directory must
# be a host path or remote filesystem; keeping it on the database volume does
# not protect against host/volume loss.

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGDATABASE:=datamid}"
: "${PGUSER:=datamid_owner}"
: "${DATAMID_DB_ADMIN_PASSWORD:?DATAMID_DB_ADMIN_PASSWORD is required}"
: "${BACKUP_ROOT:=/backups}"
: "${BACKUP_INTERVAL_SECONDS:=86400}"
: "${LOGICAL_BACKUP_RETENTION_DAYS:=14}"
: "${PITR_BASEBACKUP_INTERVAL_SECONDS:=604800}"
: "${PITR_BASEBACKUP_RETENTION_DAYS:=14}"
: "${PITR_WAL_RETENTION_DAYS:=21}"
: "${RESTORE_VERIFY_INTERVAL_SECONDS:=604800}"
: "${RESTORE_VERIFY_DATABASE:=datamid_restore_verify}"

export PGHOST PGPORT PGDATABASE PGUSER
export PGPASSWORD="$DATAMID_DB_ADMIN_PASSWORD"
umask 077

case "$BACKUP_ROOT" in
  /backups|/backups/*) ;;
  *)
    echo "BACKUP_ROOT must stay under /backups" >&2
    exit 2
    ;;
esac

case "$RESTORE_VERIFY_DATABASE" in
  "$PGDATABASE"|postgres|template0|template1|"")
    echo "RESTORE_VERIFY_DATABASE must be a dedicated non-production database" >&2
    exit 2
    ;;
esac

if [ "$PITR_WAL_RETENTION_DAYS" -le "$PITR_BASEBACKUP_RETENTION_DAYS" ]; then
  echo "PITR_WAL_RETENTION_DAYS must exceed PITR_BASEBACKUP_RETENTION_DAYS" >&2
  exit 2
fi

mkdir -p "$BACKUP_ROOT/logical" "$BACKUP_ROOT/base" "$BACKUP_ROOT/wal"

wait_for_database() {
  until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; do
    echo "Waiting for PostgreSQL at $PGHOST:$PGPORT..."
    sleep 5
  done
}

atomic_checksum() {
  file="$1"
  checksum_tmp="${file}.sha256.tmp"
  checksum_final="${file}.sha256"
  (
    cd "$(dirname "$file")"
    sha256sum "$(basename "$file")" >"$(basename "$checksum_tmp")"
  ) || return 1
  mv "$checksum_tmp" "$checksum_final" || return 1
}

marker_is_due() {
  marker="$1"
  interval="$2"
  [ "$interval" -gt 0 ] || return 1
  [ -f "$marker" ] || return 0
  now_epoch="$(date +%s)"
  last_epoch="$(cat "$marker" 2>/dev/null || printf '0')"
  [ $((now_epoch - last_epoch)) -ge "$interval" ]
}

mark_done() {
  marker="$1"
  marker_tmp="${marker}.tmp"
  date +%s >"$marker_tmp" || return 1
  mv "$marker_tmp" "$marker" || return 1
}

verify_restore() {
  dump_file="$1"
  marker="$BACKUP_ROOT/.last_restore_verify"
  marker_is_due "$marker" "$RESTORE_VERIFY_INTERVAL_SECONDS" || return 0

  echo "Running isolated restore verification into $RESTORE_VERIFY_DATABASE"
  dropdb --if-exists --force --maintenance-db=postgres "$RESTORE_VERIFY_DATABASE" || return 1
  createdb --maintenance-db=postgres "$RESTORE_VERIFY_DATABASE" || return 1

  restore_ok=0
  if pg_restore \
      --exit-on-error \
      --no-owner \
      --no-privileges \
      --dbname="$RESTORE_VERIFY_DATABASE" \
      "$dump_file"; then
    if psql --dbname="$RESTORE_VERIFY_DATABASE" --no-psqlrc --set=ON_ERROR_STOP=1 \
        --command="SELECT count(*) AS application_tables FROM information_schema.tables WHERE table_schema = 'public';"; then
      restore_ok=1
    fi
  fi

  if ! dropdb --if-exists --force --maintenance-db=postgres "$RESTORE_VERIFY_DATABASE"; then
    echo "Could not remove restore verification database" >&2
    return 1
  fi
  if [ "$restore_ok" -ne 1 ]; then
    echo "Restore verification failed" >&2
    return 1
  fi
  mark_done "$marker" || return 1
  echo "Restore verification passed"
}

run_logical_backup() {
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  final="$BACKUP_ROOT/logical/datamid-${stamp}.dump"
  tmp="${final}.tmp"

  echo "Creating logical backup $final"
  rm -f "$tmp" "${tmp}.sha256" || return 1
  if ! pg_dump \
      --format=custom \
      --compress=6 \
      --no-owner \
      --no-privileges \
      --file="$tmp" \
      "$PGDATABASE"; then
    rm -f "$tmp"
    return 1
  fi
  if ! pg_restore --list "$tmp" >/dev/null; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$final" || return 1
  atomic_checksum "$final" || return 1
  verify_restore "$final" || return 1
  echo "Logical backup verified: $final"
}

run_base_backup_if_due() {
  marker="$BACKUP_ROOT/.last_base_backup"

  # A previous release could mark a failed pg_basebackup as successful. Remove
  # incomplete directories and force an immediate retry when no manifest-backed
  # base backup exists.
  valid_base_found=0
  for candidate in "$BACKUP_ROOT"/base/base-*; do
    [ -d "$candidate" ] || continue
    if [ -s "$candidate/backup_manifest" ]; then
      valid_base_found=1
    else
      echo "Removing incomplete PITR base backup $candidate" >&2
      rm -rf -- "$candidate" || return 1
    fi
  done
  if [ "$valid_base_found" -ne 1 ]; then
    rm -f "$marker" || return 1
  fi
  marker_is_due "$marker" "$PITR_BASEBACKUP_INTERVAL_SECONDS" || return 0

  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  final="$BACKUP_ROOT/base/base-${stamp}"
  tmp="${final}.tmp"
  echo "Creating PITR base backup $final"
  rm -rf "$tmp" || return 1
  mkdir -p "$tmp" || return 1
  if ! pg_basebackup \
      --pgdata="$tmp" \
      --format=plain \
      --wal-method=stream \
      --checkpoint=fast \
      --no-password; then
    rm -rf "$tmp"
    return 1
  fi
  if ! pg_verifybackup "$tmp"; then
    rm -rf "$tmp"
    return 1
  fi
  mv "$tmp" "$final" || return 1
  mark_done "$marker" || return 1
  echo "PITR base backup verified: $final"
}

verify_wal_archiving() {
  # Force a segment switch so every successful backup cycle proves that the
  # archive_command can actually write to the external backup directory.
  if ! wal_segment="$(
      psql --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
        --command="SELECT pg_walfile_name(pg_switch_wal());" \
        "$PGDATABASE"
    )"; then
    echo "Could not switch WAL for archive verification" >&2
    return 1
  fi
  wal_segment="$(printf '%s' "$wal_segment" | tr -d '[:space:]')"
  [ -n "$wal_segment" ] || {
    echo "Could not determine switched WAL segment" >&2
    return 1
  }

  attempts=0
  while [ "$attempts" -lt 30 ]; do
    if [ -s "$BACKUP_ROOT/wal/$wal_segment" ]; then
      echo "WAL archive verified: $wal_segment"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  echo "WAL archive verification timed out for $wal_segment" >&2
  return 1
}

prune_old_backups() {
  find "$BACKUP_ROOT/logical" -type f \
    \( -name '*.dump' -o -name '*.dump.sha256' \) \
    -mtime "+$LOGICAL_BACKUP_RETENTION_DAYS" -delete

  find "$BACKUP_ROOT/base" -mindepth 1 -maxdepth 1 -type d \
    -name 'base-*' -mtime "+$PITR_BASEBACKUP_RETENTION_DAYS" \
    -exec rm -rf -- {} +

  if [ -d "$BACKUP_ROOT/wal" ]; then
    find "$BACKUP_ROOT/wal" -maxdepth 1 -type f \
      -name '[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]' \
      -mtime "+$PITR_WAL_RETENTION_DAYS" -delete
  fi
}

while :; do
  wait_for_database
  if run_logical_backup && run_base_backup_if_due && verify_wal_archiving; then
    prune_old_backups
    mark_done "$BACKUP_ROOT/.last_success"
  else
    echo "Backup cycle failed; retained existing backups and WAL" >&2
  fi
  sleep "$BACKUP_INTERVAL_SECONDS"
done
