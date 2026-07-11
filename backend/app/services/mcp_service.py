from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from backend.app.services.auth_service import _decode_signed_token, _sign_payload
from backend.app.services.llm_service import parse_json_array

MCP_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90


def now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def hash_token_value(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def create_mcp_token(user, source_keys: list[str], bind_ip: bool, ip: str, *, jti: str = "", exp_ts: int | None = None) -> str:
    clean_keys = sorted({str(item).strip() for item in source_keys if str(item).strip()})
    payload = {
        "uid": user["id"],
        "username": user["username"],
        "employee_no": user["employee_no"],
        "department": user["department"] or "",
        "source_keys": clean_keys,
        "ip": ip if bind_ip else "",
        "bind_ip": bind_ip,
        "exp": exp_ts if exp_ts is not None else int(time.time()) + MCP_TOKEN_TTL_SECONDS,
        "type": "mcp",
    }
    if jti:
        payload["jti"] = jti
    token = _sign_payload(payload)
    return f"dmc_{token}"


def decode_mcp_token(token: str) -> dict[str, Any]:
    payload = _decode_signed_token(token[4:] if token.startswith("dmc_") else token)
    if payload.get("type") != "mcp":
        raise HTTPException(status_code=401, detail="Invalid MCP token")
    return payload


def get_mcp_token_record_by_jti(conn, jti: str):
    return conn.execute("SELECT * FROM sys_mcp_token WHERE jti = ? LIMIT 1", (jti,)).fetchone()


def mark_mcp_token_used(conn, token_id: int, ip: str) -> None:
    conn.execute(
        "UPDATE sys_mcp_token SET last_used_at = ?, last_used_ip = ? WHERE id = ?",
        (now_text(), ip, token_id),
    )


def expire_stale_mcp_tokens(conn) -> None:
    conn.execute(
        "UPDATE sys_mcp_token SET status = 'expired' WHERE status = 'active' AND expires_at != '' AND expires_at < ?",
        (now_text(),),
    )


def issue_mcp_token(conn, user, source_keys: list[str], bind_ip: bool, ip: str) -> dict[str, Any]:
    requested = sorted({str(item).strip() for item in source_keys if str(item).strip()})
    issued_at = now_text()
    exp_ts = int(time.time()) + MCP_TOKEN_TTL_SECONDS
    expires_at = datetime.fromtimestamp(exp_ts).strftime("%Y-%m-%d %H:%M:%S")
    jti = secrets.token_hex(16)
    token = create_mcp_token(user, requested, bind_ip, ip, jti=jti, exp_ts=exp_ts)
    export_ip = str(ip or "").strip()
    conn.execute(
        """
        INSERT INTO sys_mcp_token (
            jti, user_id, username, employee_no, department, source_keys_json,
            bind_ip, ip, token_hash, status, created_at, expires_at,
            config_json, config_json_http
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, '', '')
        """,
        (
            jti,
            user["id"],
            user["username"],
            user["employee_no"],
            user["department"] or "",
            json.dumps(requested, ensure_ascii=False),
            1 if bind_ip else 0,
            export_ip,
            hash_token_value(token),
            issued_at,
            expires_at,
        ),
    )
    token_row = conn.execute("SELECT * FROM sys_mcp_token WHERE jti = ? LIMIT 1", (jti,)).fetchone()
    token_id = int(token_row["id"]) if token_row else 0
    return {
        "id": token_id,
        "jti": jti,
        "token": token,
        "source_keys": requested,
        "bind_ip": bind_ip,
        "ip": export_ip,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }


def update_mcp_token_configs(conn, token_id: int, config_json: str = "", config_json_http: str = "") -> None:
    conn.execute(
        "UPDATE sys_mcp_token SET config_json = ?, config_json_http = ? WHERE id = ?",
        (config_json or "", config_json_http or "", token_id),
    )


def validate_mcp_token_record(conn, token: str, payload: dict[str, Any]):
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        return None
    expire_stale_mcp_tokens(conn)
    row = get_mcp_token_record_by_jti(conn, jti)
    if not row:
        raise HTTPException(status_code=401, detail="MCP token record not found")
    if (row["token_hash"] or "") != hash_token_value(token):
        raise HTTPException(status_code=401, detail="MCP token signature mismatch")
    status = (row["status"] or "active").strip().lower()
    if status == "revoked":
        raise HTTPException(status_code=403, detail="MCP token has been disabled by admin")
    if status == "expired":
        raise HTTPException(status_code=401, detail="MCP token expired")
    return row


def serialize_mcp_token_row(row) -> dict[str, Any]:
    source_keys = parse_json_array(row["source_keys_json"])
    return {
        "id": int(row["id"]),
        "jti": row["jti"],
        "jti_short": str(row["jti"] or "")[:8],
        "user_id": int(row["user_id"] or 0),
        "username": row["username"] or "",
        "employee_no": row["employee_no"] or "",
        "department": row["department"] or "",
        "source_keys": [str(item) for item in source_keys],
        "source_count": len(source_keys),
        "bind_ip": bool(row["bind_ip"]),
        "ip": row["ip"] or "",
        "status": row["status"] or "active",
        "created_at": row["created_at"] or "",
        "expires_at": row["expires_at"] or "",
        "last_used_at": row["last_used_at"] or "",
        "last_used_ip": row["last_used_ip"] or "",
        "revoked_at": row["revoked_at"] or "",
        "revoked_by": row["revoked_by"] or "",
        "revoked_reason": row["revoked_reason"] or "",
        "user_deleted": bool(row["user_deleted"]),
        "deleted_at": row["deleted_at"] or "",
        "deleted_by": row["deleted_by"] or "",
        "deleted_reason": row["deleted_reason"] or "",
        "config_json": row.get("config_json") or "",
        "config_json_http": row.get("config_json_http") or "",
    }


def serialize_mcp_export_request_row(row) -> dict[str, Any]:
    return {
        "id": int(row["id"] or 0),
        "user_id": int(row["user_id"] or 0),
        "username": row["username"] or "",
        "employee_no": row["employee_no"] or "",
        "department": row["department"] or "",
        "source_key": row["source_key"] or "",
        "source_name": row["source_name"] or "",
        "reason": row["reason"] or "",
        "status": row["status"] or "pending",
        "created_at": row["created_at"] or "",
        "handled_at": row["handled_at"] or "",
        "handled_by": row["handled_by"] or "",
        "admin_comment": row["admin_comment"] or "",
        "user_seen": bool(row["user_seen"]),
    }
