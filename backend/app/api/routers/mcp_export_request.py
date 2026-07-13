from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.api.deps import get_current_user, require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.schemas.mcp import McpExportApplyRequest, McpExportRequestHandleRequest
from backend.app.services.auth_service import resolve_client_ip
from backend.app.services.datasource_service import get_datasource_detail
from backend.app.services.mcp_service import normalize_validity_period, now_text, serialize_mcp_export_request_row
from backend.app.services.permission_service import has_source_permission

router = APIRouter()


@router.post("/api/mcp/export-request")
def api_mcp_export_request_create(request: Request, payload: McpExportApplyRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    source_key = payload.source_key.strip()
    reason = payload.reason.strip()
    validity_period = normalize_validity_period(payload.validity_period)
    if not source_key:
        raise HTTPException(status_code=400, detail="source_key is required")
    if len(reason) < 2:
        raise HTTPException(status_code=400, detail="Please provide at least 2 characters for the request reason")
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        if user["role"] == "admin" or has_source_permission(conn, user, source_key):
            raise HTTPException(status_code=400, detail="You already have export permission for this datasource")
        pending = conn.execute(
            "SELECT * FROM sys_mcp_export_request WHERE user_id = ? AND source_key = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (user["id"], source_key),
        ).fetchone()
        if pending:
            return {"message": "Request already pending", "request": serialize_mcp_export_request_row(pending)}
        created_at = now_text()
        conn.execute(
            """
            INSERT INTO sys_mcp_export_request (
                user_id, username, employee_no, department, source_key, source_name,
                reason, status, created_at, handled_at, handled_by, admin_comment,
                validity_period
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, '', '', '', ?)
            """,
            (
                user["id"],
                user["username"],
                user["employee_no"],
                user["department"] or "",
                source_key,
                datasource["source_name"] or source_key,
                reason,
                created_at,
                validity_period,
            ),
        )
        row = conn.execute("SELECT * FROM sys_mcp_export_request ORDER BY id DESC LIMIT 1").fetchone()
        conn.commit()
    finally:
        conn.close()
    record_audit_log(
        user["username"],
        user["role"],
        "create_mcp_export_request",
        "sys_mcp_export_request",
        f"source_key={source_key};validity_period={validity_period};reason={reason}",
        client_ip,
    )
    return {"message": "Request submitted", "request": serialize_mcp_export_request_row(row)}


@router.api_route("/api/mcp/export-request/status", methods=["GET", "POST"])
def api_mcp_export_request_status(source_key: str = "", user=Depends(get_current_user)) -> dict[str, Any]:
    src = source_key.strip()
    if not src:
        raise HTTPException(status_code=400, detail="source_key is required")
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, src, include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        has_permission = user["role"] == "admin" or has_source_permission(conn, user, src)
        latest = conn.execute(
            "SELECT * FROM sys_mcp_export_request WHERE user_id = ? AND source_key = ? ORDER BY id DESC LIMIT 1",
            (user["id"], src),
        ).fetchone()
        return {
            "source_key": src,
            "source_name": datasource["source_name"] or src,
            "has_permission": has_permission,
            "latest": serialize_mcp_export_request_row(latest) if latest else None,
        }
    finally:
        conn.close()


@router.api_route("/api/mcp/export-request/my-list", methods=["GET", "POST"])
def api_mcp_export_request_my_list(user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sys_mcp_export_request WHERE user_id = ? ORDER BY id DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
        unseen_count = sum(1 for row in rows if (row["status"] or "pending") != "pending" and not row["user_seen"])
        return {"items": [serialize_mcp_export_request_row(row) for row in rows], "unseen_count": unseen_count}
    finally:
        conn.close()


@router.post("/api/mcp/export-request/mark-seen")
def api_mcp_export_request_mark_seen(user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sys_mcp_export_request SET user_seen = 1 WHERE user_id = ? AND status != 'pending' AND user_seen = 0",
            (user["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "ok"}


@router.api_route("/api/admin/mcp-export-request/summary", methods=["GET", "POST"])
def admin_mcp_export_request_summary(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        stats_rows = conn.execute("SELECT status, COUNT(*) AS total FROM sys_mcp_export_request GROUP BY status").fetchall()
        stats = {"all": 0, "pending": 0, "approved": 0, "rejected": 0}
        for row in stats_rows:
            key = str(row["status"] or "pending").strip().lower()
            if key not in stats:
                stats[key] = 0
            total = int(row["total"] or 0)
            stats[key] = total
            stats["all"] += total
        return {"pending": stats.get("pending", 0), "stats": stats}
    finally:
        conn.close()


@router.api_route("/api/admin/mcp-export-request/list", methods=["GET", "POST"])
def admin_mcp_export_request_list(
    keyword: str = "",
    status_filter: str = Query("pending", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    admin=Depends(require_admin),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        status_value = status_filter.strip().lower()
        if status_value not in {"all", "pending", "approved", "rejected"}:
            status_value = "pending"
        where_parts: list[str] = []
        args: list[Any] = []
        if status_value != "all":
            where_parts.append("status = ?")
            args.append(status_value)
        kw = keyword.strip()
        if kw:
            where_parts.append(
                "(username LIKE ? OR employee_no LIKE ? OR department LIKE ? OR source_key LIKE ? OR source_name LIKE ? OR reason LIKE ? OR admin_comment LIKE ? OR handled_by LIKE ?)"
            )
            args.extend([f"%{kw}%"] * 8)
        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(
            f"SELECT * FROM sys_mcp_export_request{where_sql} ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, id DESC LIMIT ?",
            [*args, limit],
        ).fetchall()
        stats_rows = conn.execute("SELECT status, COUNT(*) AS total FROM sys_mcp_export_request GROUP BY status").fetchall()
        stats = {"all": 0, "pending": 0, "approved": 0, "rejected": 0}
        for row in stats_rows:
            key = str(row["status"] or "pending").strip().lower()
            if key not in stats:
                stats[key] = 0
            total = int(row["total"] or 0)
            stats[key] = total
            stats["all"] += total
        return {"items": [serialize_mcp_export_request_row(row) for row in rows], "stats": stats, "status": status_value, "keyword": kw}
    finally:
        conn.close()


@router.post("/api/admin/mcp-export-request/{request_id}/handle")
def admin_mcp_export_request_handle(
    request: Request,
    request_id: int,
    payload: McpExportRequestHandleRequest,
    admin=Depends(require_admin),
) -> dict[str, Any]:
    decision = payload.status.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="status must be approved or rejected")
    admin_comment = payload.admin_comment.strip()
    if decision == "rejected" and not admin_comment:
        raise HTTPException(status_code=400, detail="Please provide a rejection reason")
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sys_mcp_export_request WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Request not found")
        current_status = str(row["status"] or "pending").strip().lower()
        if current_status != "pending":
            return {"message": "Request already handled", "request": serialize_mcp_export_request_row(row)}
        requester = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (row["user_id"],)).fetchone()
        if not requester:
            raise HTTPException(status_code=404, detail="Request user no longer exists")
        datasource = get_datasource_detail(conn, row["source_key"], include_disabled=True)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        if decision == "approved":
            direct_hit = conn.execute(
                "SELECT 1 FROM sys_user_permission WHERE user_id = ? AND source_key = ? LIMIT 1",
                (row["user_id"], row["source_key"]),
            ).fetchone()
            if not direct_hit:
                conn.execute(
                    "INSERT INTO sys_user_permission (user_id, source_key, granted_by, granted_at) VALUES (?, ?, ?, ?)",
                    (row["user_id"], row["source_key"], admin["username"], now_text()),
                )
        conn.execute(
            "UPDATE sys_mcp_export_request SET status = ?, handled_at = ?, handled_by = ?, admin_comment = ?, user_seen = 0 WHERE id = ?",
            (decision, now_text(), admin["username"], admin_comment, request_id),
        )
        updated = conn.execute("SELECT * FROM sys_mcp_export_request WHERE id = ? LIMIT 1", (request_id,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    record_audit_log(
        admin["username"],
        admin["role"],
        "handle_mcp_export_request",
        "sys_mcp_export_request",
        f"id={request_id};status={decision};user={row['username']};source_key={row['source_key']};comment={admin_comment}",
        client_ip,
    )
    return {"message": "Request approved" if decision == "approved" else "Request rejected", "request": serialize_mcp_export_request_row(updated)}
