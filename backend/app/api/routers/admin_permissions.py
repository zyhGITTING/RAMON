from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import now_text
from backend.app.schemas.user import (
    DepartmentFieldPermissionsRequest,
    DepartmentPermissionsRequest,
    FieldRestrictionRequest,
    UserFieldPermissionsRequest,
)
from backend.app.services.datasource_service import get_business_columns, get_datasource_detail
from backend.app.services.permission_service import get_restricted_field_map

router = APIRouter()


@router.get("/api/admin/department-permissions")
def admin_department_permissions(department: str = "", admin=Depends(require_admin)) -> dict[str, Any]:
    dept = department.strip()
    conn = get_connection()
    try:
        keys = [row["source_key"] for row in conn.execute("SELECT source_key FROM sys_department_permission WHERE department = ?", (dept,)).fetchall()] if dept else []
        return {"department": dept, "source_keys": keys}
    finally:
        conn.close()


@router.post("/api/admin/department-permissions")
def admin_department_permissions_save(payload: DepartmentPermissionsRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    dept = payload.department.strip()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sys_department_permission WHERE department = ?", (dept,))
        for source_key in sorted(set(payload.source_keys)):
            conn.execute("INSERT INTO sys_department_permission (department, source_key, granted_by, granted_at) VALUES (?, ?, ?, ?)", (dept, source_key, admin["username"], now_text()))
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_department_permissions", "sys_department_permission", f"department={dept};source_keys={','.join(sorted(set(payload.source_keys)))}")
        return {"message": "Department permissions updated"}
    finally:
        conn.close()


@router.get("/api/admin/user/{user_id}/field-permissions")
def admin_user_field_permissions(user_id: int, source_key: str = "", admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        src = source_key.strip()
        query = "SELECT source_key, field_name FROM sys_user_field_permission WHERE user_id = ?"
        args: list[Any] = [user_id]
        if src:
            query += " AND source_key = ?"
            args.append(src)
        granted: dict[str, list[str]] = {}
        for row in conn.execute(query, args).fetchall():
            granted.setdefault(row["source_key"], []).append(row["field_name"])
        result: dict[str, Any] = {"user_id": user_id, "granted": granted}
        if src:
            datasource = get_datasource_detail(conn, src, include_disabled=True)
            if datasource:
                columns = get_business_columns(conn, datasource)
                restricted = get_restricted_field_map(conn, src)
                result["source_key"] = src
                result["granted_fields"] = granted.get(src, [])
                result["restricted_fields"] = sorted(restricted.keys())
                result["all_fields"] = columns
        return result
    finally:
        conn.close()


@router.post("/api/admin/user/{user_id}/field-permissions")
def admin_user_field_permissions_save(user_id: int, payload: UserFieldPermissionsRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    src = payload.source_key.strip()
    if not src:
        raise HTTPException(status_code=400, detail="source_key is required")
    conn = get_connection()
    try:
        fields = sorted({f.strip() for f in payload.field_names if f.strip()})
        conn.execute("DELETE FROM sys_user_field_permission WHERE user_id = ? AND source_key = ?", (user_id, src))
        for field_name in fields:
            conn.execute(
                "INSERT INTO sys_user_field_permission (user_id, source_key, field_name, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, src, field_name, admin["username"], now_text()),
            )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_user_field_permissions", "sys_user_field_permission", f"user_id={user_id};source={src};fields={','.join(fields)}")
        return {"message": "Field permissions updated", "source_key": src, "field_names": fields}
    finally:
        conn.close()


@router.get("/api/admin/department-field-permissions")
def admin_department_field_permissions(department: str = "", source_key: str = "", admin=Depends(require_admin)) -> dict[str, Any]:
    dept = department.strip()
    src = source_key.strip()
    conn = get_connection()
    try:
        query = "SELECT source_key, field_name FROM sys_department_field_permission WHERE department = ?"
        args: list[Any] = [dept]
        if src:
            query += " AND source_key = ?"
            args.append(src)
        granted: dict[str, list[str]] = {}
        for row in (conn.execute(query, args).fetchall() if dept else []):
            granted.setdefault(row["source_key"], []).append(row["field_name"])
        return {"department": dept, "source_key": src, "granted": granted, "granted_fields": granted.get(src, [])}
    finally:
        conn.close()


@router.post("/api/admin/department-field-permissions")
def admin_department_field_permissions_save(payload: DepartmentFieldPermissionsRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    dept = payload.department.strip()
    src = payload.source_key.strip()
    if not dept or not src:
        raise HTTPException(status_code=400, detail="department and source_key are required")
    conn = get_connection()
    try:
        fields = sorted({f.strip() for f in payload.field_names if f.strip()})
        conn.execute("DELETE FROM sys_department_field_permission WHERE department = ? AND source_key = ?", (dept, src))
        for field_name in fields:
            conn.execute(
                "INSERT INTO sys_department_field_permission (department, source_key, field_name, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
                (dept, src, field_name, admin["username"], now_text()),
            )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_department_field_permissions", "sys_department_field_permission", f"department={dept};source={src};fields={','.join(fields)}")
        return {"message": "Department field permissions updated", "department": dept, "source_key": src, "field_names": fields}
    finally:
        conn.close()


@router.post("/api/admin/datasource/{source_key}/field-restriction")
def admin_set_field_restriction(source_key: str, payload: FieldRestrictionRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        existing = conn.execute(
            "SELECT id FROM sys_field_meta WHERE source_key = ? AND field_name = ? LIMIT 1",
            (source_key, payload.field_name),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE sys_field_meta SET is_restricted = ?, restricted_access = ?, mask_rule = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (1 if payload.is_restricted else 0, payload.restricted_access, payload.mask_rule, now_text(), admin["username"], existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO sys_field_meta (source_key, table_name, field_name, is_restricted, restricted_access, mask_rule, is_active, created_at, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (source_key, datasource["table_name"], payload.field_name, 1 if payload.is_restricted else 0, payload.restricted_access, payload.mask_rule, now_text(), now_text(), admin["username"]),
            )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_field_restriction", "sys_field_meta", f"source={source_key};field={payload.field_name};restricted={int(payload.is_restricted)};access={payload.restricted_access}")
        return {"message": "Field restriction updated", "source_key": source_key, "field_name": payload.field_name, "is_restricted": payload.is_restricted, "restricted_access": payload.restricted_access}
    finally:
        conn.close()
