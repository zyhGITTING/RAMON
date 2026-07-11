from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.schemas.platform import PlatformPayload, PlatformReorderRequest
from backend.app.services.platform_service import (
    create_platform,
    delete_platform,
    list_platforms,
    reorder_platforms,
    update_platform,
)

router = APIRouter()


@router.api_route("/api/admin/platform/list", methods=["GET", "POST"])
def admin_platform_list(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": list_platforms(conn)}
    finally:
        conn.close()


@router.post("/api/admin/platform/create")
def admin_platform_create(payload: PlatformPayload, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        create_platform(conn, payload.name, payload.description)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "create_platform", "sys_platform", payload.name.strip())
        return {"message": "Platform created"}
    finally:
        conn.close()


@router.put("/api/admin/platform/{platform_id}")
def admin_platform_update(platform_id: int, payload: PlatformPayload, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        update_platform(conn, platform_id, payload.name, payload.description)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "update_platform", "sys_platform", f"id={platform_id}")
        return {"message": "Platform updated"}
    finally:
        conn.close()


@router.delete("/api/admin/platform/{platform_id}")
def admin_platform_delete(platform_id: int, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        delete_platform(conn, platform_id)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "delete_platform", "sys_platform", f"id={platform_id}")
        return {"message": "Platform deleted"}
    finally:
        conn.close()


@router.post("/api/admin/platform/reorder")
def admin_platform_reorder(payload: PlatformReorderRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        reorder_platforms(conn, payload.ids)
        conn.commit()
        return {"message": "Platform order updated"}
    finally:
        conn.close()
