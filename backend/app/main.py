from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.routers.admin_audit import router as admin_audit_router
from backend.app.api.routers.admin_datasource import router as admin_datasource_router
from backend.app.api.routers.admin_datasource_control import router as admin_datasource_control_router
from backend.app.api.routers.admin_llm import router as admin_llm_router
from backend.app.api.routers.admin_mcp_token import router as admin_mcp_token_router
from backend.app.api.routers.admin_permissions import router as admin_permissions_router
from backend.app.api.routers.admin_platform import router as admin_platform_router
from backend.app.api.routers.admin_sync import router as admin_sync_router
from backend.app.api.routers.admin_users import router as admin_users_router
from backend.app.api.routers.auth import router as auth_router
from backend.app.api.routers.catalog import router as catalog_router
from backend.app.api.routers.mcp_export_request import router as mcp_export_request_router
from backend.app.api.routers.public import router as public_router
from backend.app.api.routers.public_data import router as public_data_router
from backend.app.core.config import FRONTEND_DIR
from backend.app.db.repositories.config import now_text
from backend.app.services.auth_service import hash_password
from backend.db import get_connection as get_native_connection


def _cors_allowed_origins() -> list[str]:
    return [origin.strip() for origin in os.getenv("DATAMID_CORS_ORIGINS", "").split(",") if origin.strip()]


app = FastAPI(title="Datamid", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins() or ["http://localhost:8128"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(catalog_router)
app.include_router(auth_router)
app.include_router(mcp_export_request_router)
app.include_router(admin_platform_router)
app.include_router(admin_datasource_router)
app.include_router(admin_datasource_control_router)
app.include_router(admin_mcp_token_router)
app.include_router(admin_llm_router)
app.include_router(public_data_router)
app.include_router(admin_sync_router)
app.include_router(admin_users_router)
app.include_router(admin_permissions_router)
app.include_router(admin_audit_router)
app.include_router(public_router)
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")


def _bootstrap_postgres_admin() -> None:
    """Create the first admin account when a fresh PostgreSQL database is empty."""
    init_password = os.getenv("DATAMID_INIT_ADMIN_PASSWORD", "").strip()
    if len(init_password) < 8:
        return
    conn = get_native_connection()
    try:
        if conn.execute("SELECT 1 FROM sys_user LIMIT 1").fetchone() is not None:
            return
        conn.execute(
            """
            INSERT INTO sys_user (
                employee_no, username, full_name, password_hash, role, department,
                must_change_password, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'admin', '总经办', 0, ?, ?)
            """,
            ("10001", "admin", "系统管理员", hash_password(init_password), now_text(), now_text()),
        )
        conn.commit()
    finally:
        conn.close()


def _table_column_exists(conn, table_name: str, column_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ? LIMIT 1",
        (table_name, column_name),
    ).fetchone() is not None


def _migrate_mcp_token_config_columns() -> None:
    conn = get_native_connection()
    try:
        conn.execute("ALTER TABLE sys_mcp_token ADD COLUMN IF NOT EXISTS config_json TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE sys_mcp_token ADD COLUMN IF NOT EXISTS config_json_http TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE sys_mcp_token ADD COLUMN IF NOT EXISTS validity_period TEXT NOT NULL DEFAULT '3m'")
        conn.execute("ALTER TABLE sys_mcp_export_request ADD COLUMN IF NOT EXISTS validity_period TEXT NOT NULL DEFAULT '3m'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_sync_interval_to_seconds() -> None:
    conn = get_native_connection()
    try:
        conn.execute("ALTER TABLE sys_datasource ADD COLUMN IF NOT EXISTS sync_interval_seconds INTEGER DEFAULT NULL")
        if _table_column_exists(conn, "sys_datasource", "sync_interval_minutes"):
            conn.execute(
                "UPDATE sys_datasource SET sync_interval_seconds = sync_interval_minutes * 60 "
                "WHERE sync_interval_minutes IS NOT NULL AND COALESCE(sync_interval_seconds, 0) = 0"
            )
        old_minutes = conn.execute("SELECT value FROM sys_config WHERE key = 'sync_interval_minutes' LIMIT 1").fetchone()
        if old_minutes and old_minutes["value"]:
            conn.execute(
                "INSERT INTO sys_config (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("sync_interval_seconds", str(int(old_minutes["value"]) * 60), now_text()),
            )
            conn.execute("DELETE FROM sys_config WHERE key = 'sync_interval_minutes'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_audit_log_enrichment_columns() -> None:
    conn = get_native_connection()
    new_columns = [
        ("user_id", "INTEGER DEFAULT NULL"),
        ("employee_no", "TEXT DEFAULT ''"),
        ("department", "TEXT DEFAULT ''"),
        ("token_id", "INTEGER DEFAULT NULL"),
        ("jti", "TEXT DEFAULT ''"),
        ("source_name", "TEXT DEFAULT ''"),
        ("keyword", "TEXT DEFAULT ''"),
        ("as_of", "TEXT DEFAULT ''"),
        ("page", "INTEGER DEFAULT NULL"),
        ("page_size", "INTEGER DEFAULT NULL"),
        ("row_count", "INTEGER DEFAULT NULL"),
        ("total_count", "INTEGER DEFAULT NULL"),
        ("search_fields", "TEXT DEFAULT ''"),
        ("accessed_fields", "TEXT DEFAULT ''"),
    ]
    try:
        for col, definition in new_columns:
            conn.execute(f"ALTER TABLE sys_audit_log ADD COLUMN IF NOT EXISTS {col} {definition}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


from backend.app.core.scheduler import ensure_scheduler_started

_bootstrap_postgres_admin()
_migrate_mcp_token_config_columns()
_migrate_sync_interval_to_seconds()
_migrate_audit_log_enrichment_columns()
ensure_scheduler_started()
