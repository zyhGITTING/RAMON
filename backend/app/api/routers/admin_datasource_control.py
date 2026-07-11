from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.schemas.datasource import (
    DatasourceRollbackRequest,
    DatasourceStatusRequest,
    DatasourceSyncIntervalRequest,
)
from backend.app.services.datasource_query_service import list_sync_versions
from backend.app.services.datasource_service import get_business_columns, get_datasource_detail, list_field_meta
from backend.app.services.sync_service import set_current_sync_version
from backend.app.db.repositories.config import now_text

router = APIRouter()


@router.put("/api/admin/datasource/{datasource_id}/status")
def admin_datasource_status(datasource_id: int, payload: DatasourceStatusRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute("UPDATE sys_datasource SET enabled = ?, updated_at = ? WHERE id = ?", (payload.enabled, now_text(), datasource_id))
        conn.commit()
        return {"message": "Status updated"}
    finally:
        conn.close()


@router.put("/api/admin/datasource/{source_key}/sync-interval")
def admin_datasource_sync_interval(source_key: str, payload: DatasourceSyncIntervalRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        if not get_datasource_detail(conn, source_key, include_disabled=True):
            raise HTTPException(status_code=404, detail="Datasource not found")
        conn.execute(
            "UPDATE sys_datasource SET sync_interval_seconds = ?, updated_at = ? WHERE source_key = ?",
            (payload.interval_seconds, now_text(), source_key),
        )
        conn.commit()
        return {"message": "Datasource sync interval updated"}
    finally:
        conn.close()


@router.post("/api/admin/datasource/{source_key}/rollback")
def admin_datasource_rollback(source_key: str, payload: DatasourceRollbackRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        version = set_current_sync_version(conn, datasource, payload.sync_version.strip())
        conn.commit()
        record_audit_log(
            admin["username"],
            admin["role"],
            "rollback_datasource_snapshot",
            "sys_sync_version",
            f"source_key={source_key};sync_version={payload.sync_version.strip()}",
        )
        return {
            "message": "Rollback completed",
            "source_key": source_key,
            "sync_version": version["sync_version"],
            "finished_at": version["finished_at"],
            "status": version["status"],
        }
    finally:
        conn.close()


@router.get("/api/admin/datasource/{source_key}/versions")
def admin_datasource_versions(source_key: str, limit: int = Query(50, ge=1, le=200), admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        rows = list_sync_versions(conn, source_key, limit=limit)
        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "source_key": row["source_key"],
                    "source_name": row["source_name"],
                    "table_name": row["table_name"],
                    "sync_version": row["sync_version"],
                    "sync_batch_id": row["sync_batch_id"],
                    "status": row["status"],
                    "message": row["message"] or "",
                    "row_count": int(row["row_count"] or 0),
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "duration_ms": int(row["duration_ms"] or 0),
                    "triggered_by": row["triggered_by"] or "",
                    "quality_status": row["quality_status"] or "",
                    "quality_report": row["quality_report"] or "",
                    "is_current": bool(row["is_current"]),
                }
            )
        return {"items": items}
    finally:
        conn.close()


@router.get("/api/admin/datasource/{source_key}/fields")
def admin_datasource_fields(source_key: str, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        columns = get_business_columns(conn, datasource)
        return {"items": list_field_meta(conn, datasource, columns)}
    finally:
        conn.close()
