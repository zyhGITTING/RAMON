from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from backend.app.db.repositories.config import get_config
from backend.app.services.auth_service import _decode_signed_token, _sign_payload
from backend.app.services.llm_service import parse_json_array
from backend.app.services.permission_service import has_source_permission

MCP_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90
MCP_VALIDITY_PERIODS = {
    "3m": {"days": 90, "label": "3个月"},
    "6m": {"days": 180, "label": "6个月"},
    "permanent": {"days": None, "label": "永久"},
}
MCP_PERMANENT_EXP_TS = 253402271999
MCP_PERMANENT_EXPIRES_AT = "9999-12-31 23:59:59"
MCP_TOKEN_PLACEHOLDER = "<MCP_TOKEN>"
MCP_ANOMALY_DEFAULTS = {
    "mcp_anomaly_rate_per_min": "30",
    "mcp_anomaly_rows_per_hour": "5000",
    "mcp_anomaly_distinct_ip_per_hour": "5",
    "mcp_anomaly_action": "alert_only",
}


def now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def normalize_validity_period(value: str) -> str:
    period = str(value or "").strip().lower()
    return period if period in MCP_VALIDITY_PERIODS else "3m"


def validity_period_label(value: str) -> str:
    return MCP_VALIDITY_PERIODS[normalize_validity_period(value)]["label"]


def expiry_for_validity_period(value: str) -> tuple[int, str]:
    period = normalize_validity_period(value)
    days = MCP_VALIDITY_PERIODS[period]["days"]
    if days is None:
        return MCP_PERMANENT_EXP_TS, MCP_PERMANENT_EXPIRES_AT
    exp_ts = int(time.time()) + int(days) * 24 * 60 * 60
    expires_at = datetime.fromtimestamp(exp_ts).strftime("%Y-%m-%d %H:%M:%S")
    return exp_ts, expires_at


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
    payload = _decode_signed_token(token[4:] if token.startswith("dmc_") else token, verify_exp=False)
    if payload.get("type") != "mcp":
        raise HTTPException(status_code=401, detail="Invalid MCP token")
    if not payload.get("jti") and int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="MCP token expired")
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


def issue_mcp_token(conn, user, source_keys: list[str], bind_ip: bool, ip: str, validity_period: str = "3m") -> dict[str, Any]:
    requested = sorted({str(item).strip() for item in source_keys if str(item).strip()})
    period = normalize_validity_period(validity_period)
    issued_at = now_text()
    exp_ts, expires_at = expiry_for_validity_period(period)
    jti = secrets.token_hex(16)
    token = create_mcp_token(user, requested, bind_ip, ip, jti=jti, exp_ts=exp_ts)
    export_ip = str(ip or "").strip()
    conn.execute(
        """
        INSERT INTO sys_mcp_token (
            jti, user_id, username, employee_no, department, source_keys_json,
            bind_ip, ip, token_hash, status, created_at, expires_at,
            config_json, config_json_http, validity_period
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, '', '', ?)
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
            period,
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
        "validity_period": period,
        "validity_label": validity_period_label(period),
    }


def sanitize_mcp_config(config_json: str) -> str:
    """Return a reusable config template without retaining an MCP credential."""
    raw = str(config_json or "").strip()
    if not raw:
        return ""

    def redact(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {item_key: redact(item_value, str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [redact(item, key) for item in value]
        if not isinstance(value, str):
            return value

        key_lower = key.strip().lower()
        if key_lower == "authorization":
            return f"Bearer {MCP_TOKEN_PLACEHOLDER}"
        if key_lower in {"mcp_token", "token", "access_token"}:
            return MCP_TOKEN_PLACEHOLDER

        def remove_query_secret(match: re.Match[str]) -> str:
            return match.group(1) if match.group(2) else ""

        cleaned = re.sub(
            r"([?&])(?:mcp_token|token)=[^&#\s\"']*(&?)",
            remove_query_secret,
            value,
            flags=re.IGNORECASE,
        )
        cleaned = cleaned.replace("?&", "?").rstrip("?&")
        cleaned = re.sub(r"Bearer\s+dmc_[A-Za-z0-9._~-]+", f"Bearer {MCP_TOKEN_PLACEHOLDER}", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"dmc_[A-Za-z0-9._~-]+", MCP_TOKEN_PLACEHOLDER, cleaned)
        return cleaned

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(redact(raw))
    return json.dumps(redact(parsed), ensure_ascii=False, indent=2)


def update_mcp_token_configs(conn, token_id: int, config_json: str = "", config_json_http: str = "") -> None:
    """Persist full configs including the token so users can re-view usable configs later."""
    conn.execute(
        "UPDATE sys_mcp_token SET config_json = ?, config_json_http = ? WHERE id = ?",
        (str(config_json or ""), str(config_json_http or ""), token_id),
    )


def validate_mcp_token_record(conn, token: str, payload: dict[str, Any], source_key: str = ""):
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
    requested_source = str(source_key or "").strip()
    if requested_source:
        payload_sources = {str(item).strip() for item in payload.get("source_keys", []) if str(item).strip()}
        record_sources = {str(item).strip() for item in parse_json_array(row["source_keys_json"]) if str(item).strip()}
        if requested_source not in payload_sources or requested_source not in record_sources:
            raise HTTPException(status_code=403, detail="MCP token has no access to this datasource")
        user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (row["user_id"],)).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="MCP token user no longer exists")
        if not has_source_permission(conn, user, requested_source):
            raise HTTPException(status_code=403, detail="MCP token permission has been revoked")
    return row


def record_mcp_rejection(payload: dict[str, Any], source_key: str, ip: str, reason: str) -> None:
    from backend.app.db.repositories.audit import record_audit_log

    record_audit_log(
        payload.get("username", "unknown"),
        "user",
        "mcp_token_rejected",
        source_key,
        f"reason={reason};jti={payload.get('jti') or ''}",
        ip,
        user_id=int(payload.get("uid") or 0) or None,
        employee_no=payload.get("employee_no") or "",
        department=payload.get("department") or "",
        jti=payload.get("jti") or "",
    )


def _get_int_config(conn, key: str) -> int:
    default = MCP_ANOMALY_DEFAULTS[key]
    try:
        return int(get_config(conn, key, default))
    except (TypeError, ValueError):
        return int(default)


def check_mcp_token_anomaly(conn, token_row, payload: dict[str, Any], source_key: str, ip: str) -> None:
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        return

    rate_limit = _get_int_config(conn, "mcp_anomaly_rate_per_min")
    rows_limit = _get_int_config(conn, "mcp_anomaly_rows_per_hour")
    ip_limit = _get_int_config(conn, "mcp_anomaly_distinct_ip_per_hour")
    action = get_config(conn, "mcp_anomaly_action", MCP_ANOMALY_DEFAULTS["mcp_anomaly_action"]).strip().lower()

    minute_count = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM sys_audit_log
        WHERE jti = ? AND action IN ('mcp_tool_call', 'mcp_query')
          AND created_at > NOW() - INTERVAL '1 minute'
        """,
        (jti,),
    ).fetchone()["c"]
    hour_row = conn.execute(
        """
        SELECT COALESCE(SUM(row_count), 0) AS rows_sum, COUNT(DISTINCT ip) AS ip_count
        FROM sys_audit_log
        WHERE jti = ? AND action IN ('mcp_tool_call', 'mcp_query')
          AND created_at > NOW() - INTERVAL '1 hour'
        """,
        (jti,),
    ).fetchone()

    triggered = None
    if int(minute_count or 0) >= rate_limit:
        triggered = f"rate:{minute_count}/{rate_limit} per min"
    elif int(hour_row["rows_sum"] or 0) >= rows_limit:
        triggered = f"rows:{hour_row['rows_sum']}/{rows_limit} per hour"
    elif int(hour_row["ip_count"] or 0) >= ip_limit:
        triggered = f"distinct_ip:{hour_row['ip_count']}/{ip_limit} per hour"

    if not triggered:
        return

    from backend.app.db.repositories.audit import record_audit_log

    record_audit_log(
        payload.get("username", "unknown"),
        "user",
        "mcp_anomaly_detected",
        source_key,
        f"rule={triggered};jti={jti};action={action}",
        ip,
        user_id=int(payload.get("uid") or 0) or None,
        employee_no=payload.get("employee_no") or "",
        department=payload.get("department") or "",
        token_id=int(token_row["id"]) if token_row is not None else None,
        jti=jti,
    )
    if action == "revoke":
        conn.execute(
            """
            UPDATE sys_mcp_token
            SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoked_reason = ?
            WHERE jti = ? AND status = 'active'
            """,
            (now_text(), "system", f"auto: {triggered}", jti),
        )
        conn.commit()
        raise HTTPException(status_code=403, detail="MCP token auto-revoked due to abnormal usage pattern, please contact admin")


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
        "validity_period": row.get("validity_period") or "3m",
        "validity_label": validity_period_label(row.get("validity_period") or "3m"),
        "last_used_at": row["last_used_at"] or "",
        "last_used_ip": row["last_used_ip"] or "",
        "revoked_at": row["revoked_at"] or "",
        "revoked_by": row["revoked_by"] or "",
        "revoked_reason": row["revoked_reason"] or "",
        "user_deleted": bool(row["user_deleted"]),
        "deleted_at": row["deleted_at"] or "",
        "deleted_by": row["deleted_by"] or "",
        "deleted_reason": row["deleted_reason"] or "",
        "config_json": sanitize_mcp_config(row.get("config_json") or ""),
        "config_json_http": sanitize_mcp_config(row.get("config_json_http") or ""),
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
        "validity_period": row.get("validity_period") or "3m",
        "validity_label": validity_period_label(row.get("validity_period") or "3m"),
    }
