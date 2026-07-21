#!/bin/sh
set -eu

DB_HOST="${DATAMID_DB_HOST:-postgres}"
DB_PORT="${DATAMID_DB_PORT:-5432}"
DB_NAME="${DATAMID_DB_NAME:-datamid}"
OWNER_USER="datamid_owner"
APP_USER="datamid_app"
ADMIN_PASSWORD="${DATAMID_DB_ADMIN_PASSWORD:?DATAMID_DB_ADMIN_PASSWORD is required}"
APP_PASSWORD="${DATAMID_DB_PASSWORD:?DATAMID_DB_PASSWORD is required}"

if [ "$ADMIN_PASSWORD" = "$APP_PASSWORD" ]; then
    echo "DATAMID_DB_ADMIN_PASSWORD and DATAMID_DB_PASSWORD must be different" >&2
    exit 1
fi

can_connect() {
    candidate_user="$1"
    candidate_password="$2"
    PGPASSWORD="$candidate_password" psql \
        --host "$DB_HOST" \
        --port "$DB_PORT" \
        --username "$candidate_user" \
        --dbname "$DB_NAME" \
        --no-password \
        --tuples-only \
        --command "SELECT 1" >/dev/null 2>&1
}

if can_connect "$OWNER_USER" "$ADMIN_PASSWORD"; then
    BOOTSTRAP_USER="$OWNER_USER"
    BOOTSTRAP_PASSWORD="$ADMIN_PASSWORD"
elif can_connect "datamid" "$APP_PASSWORD"; then
    # Compatibility path for volumes created by releases where the application
    # connected as the original `datamid` PostgreSQL superuser.
    BOOTSTRAP_USER="datamid"
    BOOTSTRAP_PASSWORD="$APP_PASSWORD"
else
    echo "Unable to authenticate as datamid_owner or the legacy datamid role" >&2
    exit 1
fi

PGPASSWORD="$BOOTSTRAP_PASSWORD" psql \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --username "$BOOTSTRAP_USER" \
    --dbname "$DB_NAME" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --set owner_password="$ADMIN_PASSWORD" \
    --set app_password="$APP_PASSWORD" \
    --set db_name="$DB_NAME" <<'EOSQL'
SELECT format(
    'CREATE ROLE datamid_owner WITH LOGIN SUPERUSER CREATEDB CREATEROLE INHERIT NOREPLICATION BYPASSRLS PASSWORD %L',
    :'owner_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datamid_owner')
\gexec

ALTER ROLE datamid_owner WITH LOGIN SUPERUSER CREATEDB CREATEROLE INHERIT NOREPLICATION BYPASSRLS;
SELECT format('ALTER ROLE datamid_owner PASSWORD %L', :'owner_password')
\gexec

SELECT format(
    'CREATE ROLE datamid_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datamid_app')
\gexec

ALTER ROLE datamid_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS;
SELECT format('ALTER ROLE datamid_app PASSWORD %L', :'app_password')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO datamid_owner', :'db_name')
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO datamid_app', :'db_name')
\gexec
SELECT format('GRANT TEMPORARY ON DATABASE %I TO datamid_app', :'db_name')
\gexec

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO datamid_owner;
GRANT USAGE, CREATE ON SCHEMA public TO datamid_app;

DO $roles$
DECLARE
    object_record RECORD;
    object_kind TEXT;
BEGIN
    FOR object_record IN
        SELECT namespace.nspname AS schema_name, class.relname AS object_name, class.relkind
        FROM pg_class AS class
        JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = 'public'
          AND class.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = class.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY CASE class.relkind WHEN 'S' THEN 2 ELSE 1 END, class.relname
    LOOP
        object_kind := CASE object_record.relkind
            WHEN 'S' THEN 'SEQUENCE'
            WHEN 'v' THEN 'VIEW'
            WHEN 'm' THEN 'MATERIALIZED VIEW'
            WHEN 'f' THEN 'FOREIGN TABLE'
            ELSE 'TABLE'
        END;
        EXECUTE format(
            'ALTER %s %I.%I OWNER TO datamid_app',
            object_kind,
            object_record.schema_name,
            object_record.object_name
        );
    END LOOP;
END
$roles$;

ALTER DEFAULT PRIVILEGES FOR ROLE datamid_owner IN SCHEMA public
    GRANT ALL PRIVILEGES ON TABLES TO datamid_app;
ALTER DEFAULT PRIVILEGES FOR ROLE datamid_owner IN SCHEMA public
    GRANT ALL PRIVILEGES ON SEQUENCES TO datamid_app;
ALTER DEFAULT PRIVILEGES FOR ROLE datamid_owner IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO datamid_app;
EOSQL

# Reconnect with the new owner before disabling the legacy superuser. The old
# password is deliberately cleared because it becomes the runtime app password.
PGPASSWORD="$ADMIN_PASSWORD" psql \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --username "$OWNER_USER" \
    --dbname "$DB_NAME" \
    --no-password \
    --set ON_ERROR_STOP=1 <<'EOSQL'
SELECT format('REVOKE datamid FROM %I', member_role.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS member_role ON member_role.oid = membership.member
JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
WHERE granted_role.rolname = 'datamid'
\gexec

-- PostgreSQL does not allow removing SUPERUSER from the bootstrap role (OID
-- 10). On legacy volumes that role is named `datamid`; clearing its password,
-- disabling LOGIN and removing every membership makes it unreachable. A
-- non-bootstrap legacy role is additionally demoted from SUPERUSER.
SELECT CASE
    WHEN oid = 10 THEN
        'ALTER ROLE datamid WITH NOLOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL'
    ELSE
        'ALTER ROLE datamid WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL'
    END
FROM pg_roles
WHERE rolname = 'datamid'
\gexec

DO $verify$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'datamid_app'
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'datamid_app has forbidden cluster privileges';
    END IF;
    IF NOT has_database_privilege('datamid_app', current_database(), 'TEMPORARY') THEN
        RAISE EXCEPTION 'datamid_app is missing the TEMPORARY database privilege';
    END IF;
END
$verify$;
EOSQL

echo "Database roles are configured: owner=datamid_owner runtime=datamid_app"
