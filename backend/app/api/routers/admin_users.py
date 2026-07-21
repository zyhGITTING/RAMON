from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import now_text
from backend.app.schemas.user import (
    UserCreateRequest,
    UserDeleteRequest,
    UserDepartmentRequest,
    UserPermissionsRequest,
)
from backend.app.services.auth_service import (
    generate_default_temp_password,
    hash_password,
    serialize_user,
    validate_password_strength,
    verify_password,
)
from backend.app.services.permission_service import resolve_permission_origin

router = APIRouter()


@router.api_route("/api/admin/stats", methods=["GET", "POST"])
def admin_stats(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {
            "user_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_user").fetchone()["c"]),
            "platform_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_platform").fetchone()["c"]),
            "datasource_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_datasource").fetchone()["c"]),
            "enabled_datasource_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_datasource WHERE enabled = 1").fetchone()["c"]),
            "sync_log_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_sync_log").fetchone()["c"]),
            "audit_log_count": int(conn.execute("SELECT COUNT(*) AS c FROM sys_audit_log").fetchone()["c"]),
        }
    finally:
        conn.close()


@router.api_route("/api/admin/user/list", methods=["GET", "POST"])
def admin_user_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    keyword: str = Query("", max_length=100),
    admin=Depends(require_admin),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        params: list[Any] = []
        where_sql = ""
        if keyword.strip():
            pattern = f"%{keyword.strip().lower()}%"
            where_sql = (
                " WHERE LOWER(COALESCE(employee_no, '')) LIKE ?"
                " OR LOWER(COALESCE(username, '')) LIKE ?"
                " OR LOWER(COALESCE(full_name, '')) LIKE ?"
                " OR LOWER(COALESCE(department, '')) LIKE ?"
            )
            params = [pattern, pattern, pattern, pattern]
        total = int(conn.execute(f"SELECT COUNT(*) AS total FROM sys_user{where_sql}", params).fetchone()["total"])
        rows = conn.execute(
            f"SELECT * FROM sys_user{where_sql} ORDER BY id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "items": [serialize_user(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


@router.post("/api/admin/user/create")
def admin_user_create(payload: UserCreateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    username = payload.username.strip()
    employee_no = payload.employee_no.strip()
    error = validate_password_strength(payload.password, username=username, employee_no=employee_no)
    if error:
        raise HTTPException(status_code=400, detail=error)
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM sys_user WHERE username = ? OR employee_no = ? LIMIT 1", (username, employee_no)).fetchone():
            raise HTTPException(status_code=400, detail="Username or employee number already exists")
        conn.execute(
            "INSERT INTO sys_user (employee_no, username, full_name, password_hash, role, department, must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (employee_no, username, payload.full_name.strip(), hash_password(payload.password), payload.role, payload.department.strip(), now_text(), now_text()),
        )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "create_user", "sys_user", username)
        return {"message": "User created"}
    finally:
        conn.close()


@router.get("/api/admin/user/{user_id}/permissions")
def admin_user_permissions(user_id: int, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        direct = [row["source_key"] for row in conn.execute("SELECT source_key FROM sys_user_permission WHERE user_id = ?", (user_id,)).fetchall()]
        dept = (user["department"] or "").strip()
        dept_keys = [row["source_key"] for row in conn.execute("SELECT source_key FROM sys_department_permission WHERE department = ?", (dept,)).fetchall()] if dept else []
        effective = sorted(set(direct) | set(dept_keys))
        origins = {key: resolve_permission_origin(conn, user, key) for key in effective}
        return {
            "department": dept,
            "source_keys": direct,
            "direct_source_keys": direct,
            "department_source_keys": dept_keys,
            "effective_source_keys": effective,
            "permission_origins": origins,
        }
    finally:
        conn.close()


@router.put("/api/admin/user/{user_id}/department")
def admin_user_department(user_id: int, payload: UserDepartmentRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute("UPDATE sys_user SET department = ?, updated_at = ? WHERE id = ?", (payload.department.strip(), now_text(), user_id))
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_department", "sys_user", f"user_id={user_id};department={payload.department.strip()}")
        return {"message": "Department updated"}
    finally:
        conn.close()


@router.post("/api/admin/user/{user_id}/permissions")
def admin_user_permissions_save(user_id: int, payload: UserPermissionsRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sys_user_permission WHERE user_id = ?", (user_id,))
        for source_key in sorted(set(payload.source_keys)):
            conn.execute("INSERT INTO sys_user_permission (user_id, source_key, granted_by, granted_at) VALUES (?, ?, ?, ?)", (user_id, source_key, admin["username"], now_text()))
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "set_user_permissions", "sys_user_permission", f"user_id={user_id};source_keys={','.join(sorted(set(payload.source_keys)))}")
        return {"message": "Permissions updated"}
    finally:
        conn.close()


@router.post("/api/admin/user/{user_id}/reset-password")
def admin_user_reset_password(user_id: int, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        target = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        temp_password = generate_default_temp_password(target["employee_no"])
        conn.execute(
            "UPDATE sys_user SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE id = ?",
            (hash_password(temp_password), now_text(), user_id),
        )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "reset_password", "sys_user", f"id={user_id};username={target['username']}")
        return {"message": "Password reset", "username": target["username"], "temp_password": temp_password}
    finally:
        conn.close()


@router.post("/api/admin/user/{user_id}/delete")
def admin_user_delete(user_id: int, payload: UserDeleteRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    conn = get_connection()
    try:
        target = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if not verify_password(payload.admin_password, admin["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid admin password")
        if target["role"] == "admin":
            admin_count = int(conn.execute("SELECT COUNT(*) AS c FROM sys_user WHERE role = 'admin'").fetchone()["c"])
            if admin_count <= 1:
                raise HTTPException(status_code=400, detail="Cannot delete the last remaining admin account")
        username = target["username"]
        conn.execute("DELETE FROM sys_user_permission WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sys_user_field_permission WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sys_mcp_token WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sys_mcp_export_request WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sys_user WHERE id = ?", (user_id,))
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "delete_user", "sys_user", f"id={user_id};username={username}")
        return {"message": "User deleted", "id": user_id, "username": username}
    finally:
        conn.close()
