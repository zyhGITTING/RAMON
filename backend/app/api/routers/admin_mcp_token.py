from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.app.api.deps import get_current_user, require_admin
from backend.app.core.config import APP_PORT
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.schemas.mcp import McpExportRequest, McpTokenRevokeRequest
from backend.app.services.auth_service import resolve_client_ip
from backend.app.services.mcp_service import expire_stale_mcp_tokens, issue_mcp_token, now_text, serialize_mcp_token_row, update_mcp_token_configs
from backend.app.services.permission_service import get_effective_source_keys

router = APIRouter()


PUBLIC_URL = os.getenv("DATAMID_PUBLIC_URL", f"http://localhost:{APP_PORT}").rstrip("/")


def _source_name_map(conn) -> dict[str, str]:
    rows = conn.execute("SELECT source_key, source_name FROM sys_datasource").fetchall()
    return {str(row["source_key"] or ""): str(row["source_name"] or row["source_key"] or "") for row in rows}


def _serialize_token_items(conn, rows) -> list[dict[str, Any]]:
    source_names = _source_name_map(conn)
    items: list[dict[str, Any]] = []
    for row in rows:
        item = serialize_mcp_token_row(row)
        item["source_names"] = [source_names.get(key, key) for key in item["source_keys"]]
        items.append(item)
    return items


SSE_ENDPOINT_TEMPLATE = "{public_url}/api/mcp/sse/{item}?mcp_token={token}"
HTTP_ENDPOINT_TEMPLATE = "{public_url}/api/mcp/data/{item}?mcp_token={token}"


def _build_mcp_config(token: str, source_keys: list[str], endpoint_template: str, public_url: str) -> str:
    servers = {}
    for idx, item in enumerate(source_keys):
        key = "ramon-datamid" if len(source_keys) == 1 and idx == 0 else f"ramon-datamid-{item}"
        url = endpoint_template.format(public_url=public_url, item=item, token=token)
        servers[key] = {"url": url, "description": f"Datamid MCP endpoint for {item}"}
    return json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2)


def _build_mcp_export_response(
    issued: dict[str, Any],
    config_json: str,
    audit_action: str,
    user,
    client_ip: str,
) -> dict[str, Any]:
    record_audit_log(
        user["username"],
        user["role"],
        audit_action,
        "sys_datasource",
        f"source_keys={','.join(issued['source_keys'])};token_id={issued['id']}",
        client_ip,
        user_id=int(user["id"]),
        employee_no=user["employee_no"] or "",
        department=user["department"] or "",
        token_id=int(issued["id"]),
        jti=issued.get("jti") or "",
    )
    return {
        "token": issued["token"],
        "token_id": issued["id"],
        "issued_at": issued["issued_at"],
        "expires_at": issued["expires_at"],
        "department": user["department"] or "",
        "source_keys": issued["source_keys"],
        "bind_ip": issued.get("bind_ip", False),
        "managed": True,
        "config_json": config_json,
    }


@router.post("/api/mcp/export")
def api_mcp_export(request: Request, payload: McpExportRequest, source_key: str = Query("", alias="source_key"), user=Depends(get_current_user)) -> dict[str, Any]:
    """导出 MCP SSE 配置。"""
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        allowed = get_effective_source_keys(conn, user)
        requested = payload.source_keys[:] if payload.source_keys else ([source_key] if source_key else sorted(allowed))
        if user["role"] != "admin":
            denied = [item for item in requested if item not in allowed]
            if denied:
                raise HTTPException(status_code=403, detail=f"No export permission for: {', '.join(denied)}")
        issued = issue_mcp_token(conn, user, requested, payload.bind_ip, client_ip)
        config_json = _build_mcp_config(issued["token"], issued["source_keys"], SSE_ENDPOINT_TEMPLATE, PUBLIC_URL)
        config_json_http = _build_mcp_config(issued["token"], issued["source_keys"], HTTP_ENDPOINT_TEMPLATE, PUBLIC_URL)
        update_mcp_token_configs(conn, issued["id"], config_json, config_json_http)
        conn.commit()
    finally:
        conn.close()
    return _build_mcp_export_response(issued, config_json, "mcp_export", user, client_ip)


@router.post("/api/mcp/export-http")
def api_mcp_export_http(request: Request, payload: McpExportRequest, source_key: str = Query("", alias="source_key"), user=Depends(get_current_user)) -> dict[str, Any]:
    """导出 MCP Streamable HTTP 配置（直接 POST JSON-RPC 到该 URL）。"""
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        allowed = get_effective_source_keys(conn, user)
        requested = payload.source_keys[:] if payload.source_keys else ([source_key] if source_key else sorted(allowed))
        if user["role"] != "admin":
            denied = [item for item in requested if item not in allowed]
            if denied:
                raise HTTPException(status_code=403, detail=f"No export permission for: {', '.join(denied)}")
        issued = issue_mcp_token(conn, user, requested, payload.bind_ip, client_ip)
        config_json = _build_mcp_config(issued["token"], issued["source_keys"], SSE_ENDPOINT_TEMPLATE, PUBLIC_URL)
        config_json_http = _build_mcp_config(issued["token"], issued["source_keys"], HTTP_ENDPOINT_TEMPLATE, PUBLIC_URL)
        update_mcp_token_configs(conn, issued["id"], config_json, config_json_http)
        conn.commit()
    finally:
        conn.close()
    return _build_mcp_export_response(issued, config_json_http, "mcp_export_http", user, client_ip)


@router.api_route("/api/admin/mcp-token/list", methods=["GET", "POST"])
def admin_mcp_token_list(
    keyword: str = "",
    status_filter: str = Query("all", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    admin=Depends(require_admin),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        conn.commit()
        status_value = status_filter.strip().lower()
        if status_value not in {"all", "active", "revoked", "expired", "deleted"}:
            status_value = "all"
        where_parts: list[str] = []
        args: list[Any] = []
        if status_value != "all":
            where_parts.append("status = ?")
            args.append(status_value)
        kw = keyword.strip()
        if kw:
            where_parts.append(
                "(username LIKE ? OR employee_no LIKE ? OR department LIKE ? OR source_keys_json LIKE ? OR ip LIKE ? OR last_used_ip LIKE ?)"
            )
            args.extend([f"%{kw}%"] * 6)
        where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(f"SELECT * FROM sys_mcp_token{where_sql} ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        stats_rows = conn.execute("SELECT status, COUNT(*) AS total FROM sys_mcp_token GROUP BY status").fetchall()
        stats = {"all": 0, "active": 0, "revoked": 0, "expired": 0, "deleted": 0}
        for row in stats_rows:
            status_key = str(row["status"] or "active")
            stats[status_key] = int(row["total"] or 0)
            stats["all"] += int(row["total"] or 0)
        return {"items": _serialize_token_items(conn, rows), "stats": stats, "status": status_value, "keyword": kw}
    finally:
        conn.close()


@router.api_route("/api/mcp/token/my-list", methods=["GET", "POST"])
def my_mcp_token_list(user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM sys_mcp_token WHERE user_id = ? AND COALESCE(user_deleted, 0) = 0 ORDER BY id DESC LIMIT 200",
            (user["id"],),
        ).fetchall()
        items = _serialize_token_items(conn, rows)
        stats = {"all": len(items), "active": 0, "revoked": 0, "expired": 0}
        for item in items:
            status_key = str(item["status"] or "active").strip().lower()
            if status_key in stats:
                stats[status_key] += 1
        return {"items": items, "stats": stats}
    finally:
        conn.close()


@router.post("/api/mcp/token/{token_id}/revoke")
def my_mcp_token_revoke(request: Request, token_id: int, payload: McpTokenRevokeRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        row = conn.execute("SELECT * FROM sys_mcp_token WHERE id = ? LIMIT 1", (token_id,)).fetchone()
        if not row or int(row["user_id"] or 0) != int(user["id"]):
            raise HTTPException(status_code=404, detail="MCP token not found")
        if int(row["user_deleted"] or 0):
            return {"message": "MCP token already deleted", "id": token_id}
        status_value = str(row["status"] or "active").strip().lower()
        if status_value == "revoked":
            return {"message": "MCP token already disabled", "id": token_id}
        if status_value == "expired":
            return {"message": "MCP token already expired", "id": token_id}
        reason = payload.reason.strip()
        conn.execute(
            "UPDATE sys_mcp_token SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoked_reason = ? WHERE id = ?",
            (now_text(), user["username"], reason, token_id),
        )
        conn.commit()
    finally:
        conn.close()
    record_audit_log(user["username"], user["role"], "revoke_mcp_token_self", "sys_mcp_token", f"id={token_id};reason={reason}", client_ip)
    return {"message": "MCP token disabled", "id": token_id}


@router.post("/api/mcp/token/{token_id}/delete")
def my_mcp_token_delete(request: Request, token_id: int, payload: McpTokenRevokeRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    client_ip = resolve_client_ip(request)
    deleted_at = now_text()
    reason = payload.reason.strip()
    revoke_reason = reason or "self_deleted"
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        row = conn.execute("SELECT * FROM sys_mcp_token WHERE id = ? LIMIT 1", (token_id,)).fetchone()
        if not row or int(row["user_id"] or 0) != int(user["id"]):
            raise HTTPException(status_code=404, detail="MCP token not found")
        if int(row["user_deleted"] or 0):
            return {"message": "MCP token already deleted", "id": token_id}
        status_value = str(row["status"] or "active").strip().lower()
        if status_value == "active":
            conn.execute(
                """
                UPDATE sys_mcp_token
                SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoked_reason = ?,
                    user_deleted = 1, deleted_at = ?, deleted_by = ?, deleted_reason = ?
                WHERE id = ?
                """,
                (deleted_at, user["username"], revoke_reason, deleted_at, user["username"], reason, token_id),
            )
        else:
            conn.execute(
                "UPDATE sys_mcp_token SET user_deleted = 1, deleted_at = ?, deleted_by = ?, deleted_reason = ? WHERE id = ?",
                (deleted_at, user["username"], reason, token_id),
            )
        conn.commit()
    finally:
        conn.close()
    record_audit_log(user["username"], user["role"], "delete_mcp_token_self", "sys_mcp_token", f"id={token_id};reason={reason}", client_ip)
    return {"message": "MCP token deleted", "id": token_id}


@router.api_route("/api/mcp/token/{token_id}/config", methods=["GET", "POST"])
def my_mcp_token_config(token_id: int, user=Depends(get_current_user)) -> dict[str, Any]:
    """查看自己某条 MCP 令牌导出时的原文配置（SSE / HTTP 两种协议都返回）。"""
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        row = conn.execute("SELECT * FROM sys_mcp_token WHERE id = ? LIMIT 1", (token_id,)).fetchone()
        if not row or int(row["user_id"] or 0) != int(user["id"]):
            raise HTTPException(status_code=404, detail="MCP token not found")
        return {
            "id": token_id,
            "source_keys": [str(item) for item in json.loads(row["source_keys_json"] or "[]")],
            "config_json": row.get("config_json") or "",
            "config_json_http": row.get("config_json_http") or "",
            "status": row["status"] or "active",
            "created_at": row["created_at"] or "",
            "expires_at": row["expires_at"] or "",
        }
    finally:
        conn.close()


@router.api_route("/api/admin/mcp-token/{token_id}/config", methods=["GET", "POST"])
def admin_mcp_token_config(token_id: int, admin=Depends(require_admin)) -> dict[str, Any]:
    """管理员查看任意 MCP 令牌的原文配置。"""
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        row = conn.execute("SELECT * FROM sys_mcp_token WHERE id = ? LIMIT 1", (token_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MCP token not found")
        return {
            "id": token_id,
            "username": row["username"] or "",
            "employee_no": row["employee_no"] or "",
            "source_keys": [str(item) for item in json.loads(row["source_keys_json"] or "[]")],
            "config_json": row.get("config_json") or "",
            "config_json_http": row.get("config_json_http") or "",
            "status": row["status"] or "active",
            "created_at": row["created_at"] or "",
            "expires_at": row["expires_at"] or "",
        }
    finally:
        conn.close()


@router.post("/api/admin/mcp-token/{token_id}/revoke")
def admin_mcp_token_revoke(token_id: int, payload: McpTokenRevokeRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        expire_stale_mcp_tokens(conn)
        row = conn.execute("SELECT * FROM sys_mcp_token WHERE id = ? LIMIT 1", (token_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MCP token not found")
        status_value = str(row["status"] or "active").strip().lower()
        if status_value == "revoked":
            return {"message": "MCP token already revoked", "id": token_id}
        if status_value == "expired":
            return {"message": "MCP token already expired", "id": token_id}
        reason = payload.reason.strip()
        conn.execute(
            "UPDATE sys_mcp_token SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoked_reason = ? WHERE id = ?",
            (now_text(), admin["username"], reason, token_id),
        )
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "revoke_mcp_token", "sys_mcp_token", f"id={token_id};user={row['username']};reason={reason}")
        return {"message": "MCP token revoked", "id": token_id}
    finally:
        conn.close()
