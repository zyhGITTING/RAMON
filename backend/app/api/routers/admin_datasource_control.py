from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.schemas.datasource import (
    DatasourceFieldMetadataRequest,
    DatasourceRollbackRequest,
    DatasourceStatusRequest,
    DatasourceSyncIntervalRequest,
)
from backend.app.services.datasource_query_service import list_sync_versions
from backend.app.services.datasource_service import (
    STANDARD_FIELD_CATALOG,
    get_business_columns,
    get_datasource_detail,
    list_field_meta,
)
from backend.app.services.sync_service import reset_datasource_checkpoint, set_current_sync_version
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
        standard_catalog = [
            {"code": code, **metadata}
            for code, metadata in sorted(STANDARD_FIELD_CATALOG.items())
        ]
        return {"items": list_field_meta(conn, datasource, columns), "standard_catalog": standard_catalog}
    finally:
        conn.close()


@router.post("/api/admin/datasource/{source_key}/fields")
def admin_datasource_fields_save(source_key: str, payload: DatasourceFieldMetadataRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")

        allowed_fields = set(get_business_columns(conn, datasource))
        submitted_names = [item.field_name.strip() for item in payload.items]
        if len(submitted_names) != len(set(submitted_names)):
            raise HTTPException(status_code=400, detail="Duplicate field_name in request")
        unknown_fields = sorted(name for name in submitted_names if name not in allowed_fields)
        if unknown_fields:
            raise HTTPException(status_code=400, detail=f"Unknown datasource fields: {', '.join(unknown_fields)}")

        now = now_text()
        for item in payload.items:
            field_name = item.field_name.strip()
            field_label = item.field_label.strip() or field_name
            standard_code = item.standard_field_code.strip()
            catalog = STANDARD_FIELD_CATALOG.get(standard_code, {}) if standard_code else {}
            standard_name = (
                item.standard_field_name.strip() or str(catalog.get("standard_field_name") or field_label)
                if standard_code
                else field_label
            )
            business_domain = item.business_domain.strip() or str(catalog.get("business_domain") or "")
            definition = item.definition.strip() or str(catalog.get("definition") or "")
            existing = conn.execute(
                "SELECT id FROM sys_field_meta WHERE source_key = ? AND field_name = ? LIMIT 1",
                (source_key, field_name),
            ).fetchone()
            values = (
                datasource["table_name"],
                field_label,
                standard_code,
                standard_name,
                business_domain,
                str(catalog.get("entity_code") or ""),
                str(catalog.get("entity_role") or ""),
                str(catalog.get("metric_unit") or ""),
                definition,
                1 if item.is_restricted else 0,
                item.restricted_access,
                item.mask_rule.strip(),
                now,
                admin["username"],
            )
            if existing:
                conn.execute(
                    """
                    UPDATE sys_field_meta
                    SET table_name = ?, field_label = ?, standard_field_code = ?, standard_field_name = ?,
                        business_domain = ?, entity_code = ?, entity_role = ?, metric_unit = ?, definition = ?,
                        is_restricted = ?, restricted_access = ?, mask_rule = ?, is_active = 1,
                        updated_at = ?, updated_by = ?
                    WHERE id = ?
                    """,
                    (*values, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sys_field_meta (
                        source_key, table_name, field_name, field_label, source_path,
                        standard_field_code, standard_field_name, business_domain, entity_code, entity_role,
                        metric_unit, definition, is_restricted, restricted_access, mask_rule,
                        is_active, created_at, updated_at, updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        source_key,
                        datasource["table_name"],
                        field_name,
                        field_label,
                        field_name,
                        standard_code,
                        standard_name,
                        business_domain,
                        str(catalog.get("entity_code") or ""),
                        str(catalog.get("entity_role") or ""),
                        str(catalog.get("metric_unit") or ""),
                        definition,
                        1 if item.is_restricted else 0,
                        item.restricted_access,
                        item.mask_rule.strip(),
                        now,
                        now,
                        admin["username"],
                    ),
                )
        conn.commit()
        record_audit_log(
            admin["username"],
            admin["role"],
            "update_field_metadata",
            "sys_field_meta",
            f"source={source_key};fields={len(payload.items)}",
        )
        return {
            "message": "Field metadata updated",
            "items": list_field_meta(conn, datasource, get_business_columns(conn, datasource)),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/api/admin/datasource/{source_key}/checkpoint-reset")
def admin_datasource_checkpoint_reset(source_key: str, admin=Depends(require_admin)) -> dict[str, Any]:
    """Truncate staging and reset the sync checkpoint so the next run starts fresh."""
    conn = get_connection()
    try:
        result = reset_datasource_checkpoint(conn, source_key, force_full_sync=True)
        conn.commit()
        record_audit_log(
            admin["username"],
            admin["role"],
            "reset_sync_checkpoint",
            "sys_sync_checkpoint",
            f"source_key={source_key}",
        )
        return result
    finally:
        conn.close()
