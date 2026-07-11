from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import get_config, now_text, set_config
from backend.app.schemas.sync import SyncConfigRequest, SyncTriggerRequest
from backend.app.services.sync_progress_service import (
    is_sync_running,
    request_sync_cancel,
    snapshot_sync_progress,
)
from backend.app.services.sync_service import (
    DEFAULT_SYNC_INTERVAL_SECONDS,
    get_sync_status_payload,
    launch_sync_job,
)

router = APIRouter()


@router.api_route("/api/admin/sync-log", methods=["GET", "POST"])
def admin_sync_log(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        total = int(conn.execute("SELECT COUNT(*) AS c FROM sys_sync_log").fetchone()["c"])
        rows = conn.execute("SELECT * FROM sys_sync_log ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


@router.get("/api/admin/sync/status")
def admin_sync_status(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return get_sync_status_payload(conn)
    finally:
        conn.close()


@router.get("/api/admin/sync/progress")
def admin_sync_progress(admin=Depends(require_admin)) -> dict[str, Any]:
    return snapshot_sync_progress()


@router.put("/api/admin/sync/config")
def admin_sync_config(payload: SyncConfigRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        if payload.auto_enabled is not None:
            set_config(conn, "auto_sync_enabled", "1" if payload.auto_enabled else "0")
            if payload.auto_enabled:
                # 让调度器立即按各数据源间隔重新评估
                set_config(conn, "next_auto_sync_at", now_text())
            else:
                set_config(conn, "next_auto_sync_at", "")
        if payload.interval_seconds is not None:
            set_config(conn, "sync_interval_seconds", str(payload.interval_seconds))
            if get_config(conn, "auto_sync_enabled", "0") == "1":
                set_config(conn, "next_auto_sync_at", now_text())
        conn.commit()
        return {
            "message": "Updated",
            "auto_enabled": get_config(conn, "auto_sync_enabled", "0") == "1",
            "interval_seconds": int(get_config(conn, "sync_interval_seconds", str(DEFAULT_SYNC_INTERVAL_SECONDS)) or DEFAULT_SYNC_INTERVAL_SECONDS),
        }
    finally:
        conn.close()


@router.post("/api/admin/sync/cancel")
def admin_sync_cancel(admin=Depends(require_admin)) -> dict[str, Any]:
    if not is_sync_running():
        raise HTTPException(status_code=400, detail="No active sync task")
    request_sync_cancel()
    return {"message": "Cancel requested"}


@router.post("/api/admin/sync/trigger")
def admin_sync_trigger(payload: SyncTriggerRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    result = launch_sync_job(payload.source_key, admin["username"])
    record_audit_log(admin["username"], admin["role"], "trigger_sync", "sys_sync_log", f"source={payload.source_key or 'all'}")
    return result
