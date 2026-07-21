from __future__ import annotations

import hmac
import json
import os
import queue
import threading
import time
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from starlette.responses import Response, StreamingResponse

from backend.app.api.deps import get_current_user
from backend.app.core.config import APP_PORT, MCP_DEFAULT_PAGE_SIZE, MCP_MAX_PAGE_SIZE
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.services.auth_service import resolve_client_ip, security
from backend.app.services.datasource_query_service import BUSINESS_DETAIL_SOURCE_KEYS, query_datasource_rows
from backend.app.services.datasource_service import (
    get_business_columns,
    get_datasource_detail,
    list_field_meta,
    parse_datasource_config,
    serialize_datasource,
)
from backend.app.services.mcp_service import (
    check_mcp_token_anomaly,
    decode_mcp_token,
    mark_mcp_token_used,
    record_mcp_rejection,
    validate_mcp_token_record,
)
from backend.app.services.permission_service import has_source_permission

router = APIRouter()

PUBLIC_URL = os.getenv("DATAMID_PUBLIC_URL", f"http://localhost:{APP_PORT}").rstrip("/")


def _validate_mcp_token_with_audit(conn, raw_token: str, payload: dict[str, Any], source_key: str, request: Request):
    try:
        return validate_mcp_token_record(conn, raw_token, payload, source_key)
    except HTTPException as exc:
        record_mcp_rejection(payload, source_key, resolve_client_ip(request), str(exc.detail))
        raise


@router.get("/api/data/{source_key}")
def api_data(
    source_key: str,
    request: Request,
    keyword: str = "",
    as_of: str = "",
    sync_version: str = "",
    start_time: str = "",
    end_time: str = "",
    start_date: str = "",
    end_date: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user=Depends(get_current_user),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        allowed = has_source_permission(conn, user, source_key)
        result = query_datasource_rows(
            conn,
            datasource,
            keyword,
            page,
            page_size,
            allowed or user["role"] == "admin",
            as_of=as_of,
            sync_version=sync_version,
            start_time=start_time,
            end_time=end_time,
            start_date=start_date,
            end_date=end_date,
            user=user,
        )
        record_audit_log(
            user["username"],
            user["role"],
            "view_data",
            source_key,
            f"keyword={keyword.strip() or ''};preview_only={int(result['preview_only'])};as_of={as_of.strip()};sync_version={sync_version.strip()};start_time={start_time.strip()};end_time={end_time.strip()};business_time_field={result.get('business_time_field', '')}",
            resolve_client_ip(request),
            user_id=int(user["id"]),
            employee_no=user["employee_no"] or "",
            department=user["department"] or "",
            source_name=datasource["source_name"] or source_key,
            keyword=keyword.strip(),
            as_of=as_of.strip(),
            start_time=result.get("start_time", ""),
            end_time=result.get("end_time", ""),
            business_time_field=result.get("business_time_field", ""),
            page=page,
            page_size=page_size,
            row_count=len(result.get("rows", [])),
            total_count=int(result.get("total") or 0),
            search_fields=",".join(result.get("search_fields", [])),
            accessed_fields=",".join(result.get("columns", [])),
        )
        return result
    finally:
        conn.close()


def _extract_mcp_token(request: Request) -> str:
    """Extract MCP token from Authorization header or URL query parameter (legacy)."""
    auth = request.headers.get("Authorization", "")
    raw_token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if raw_token:
        return raw_token
    return str(request.query_params.get("mcp_token") or request.query_params.get("token") or "").strip()


def _authenticate_mcp_token(
    credentials: HTTPAuthorizationCredentials | None,
    source_key: str,
    request: Request,
) -> tuple[str, dict[str, Any]]:
    raw_token = _extract_mcp_token(request)
    if not raw_token and credentials is not None:
        raw_token = credentials.credentials.strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing MCP token")
    payload = decode_mcp_token(raw_token)
    if source_key not in payload.get("source_keys", []):
        raise HTTPException(status_code=403, detail="MCP token has no access to this datasource")
    return raw_token, payload


def _make_json_serializable(value: Any) -> Any:
    """递归把 datetime/date 对象转成 ISO 字符串，保证 json.dumps 不报错。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _make_json_serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_json_serializable(v) for v in value]
    return value


def _build_mcp_tool(source_key: str, datasource: Any, field_meta: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source_name = datasource["source_name"] or source_key
    config = parse_datasource_config(datasource)
    business_time_field = str(config.get("business_time_field") or "").strip()
    is_business_detail = source_key in BUSINESS_DETAIL_SOURCE_KEYS
    properties: dict[str, Any] = {
        "keyword": {"type": "string", "description": "搜索关键词，支持配置字段的不区分大小写模糊查询"},
        "page": {"type": "integer", "description": "页码，默认 1", "minimum": 1},
        "page_size": {
            "type": "integer",
            "description": f"每页条数，默认 {MCP_DEFAULT_PAGE_SIZE}，最大 {MCP_MAX_PAGE_SIZE}",
            "minimum": 1,
            "maximum": MCP_MAX_PAGE_SIZE,
            "default": MCP_DEFAULT_PAGE_SIZE,
        },
        "as_of": {"type": "string", "description": "历史快照时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS（可选）"},
        "filters": {
            "type": "object",
            "description": "字段级过滤条件，键为字段名/中文标签/标准字段名，值为要包含的字符串；多个条件之间是 AND 关系，使用不区分大小写的包含匹配。必须把用户指定的公司、员工、单号等条件放入此处，由服务端在完整数据集上先筛选。",
            "additionalProperties": {"type": "string"},
        },
    }
    required: list[str] = []
    if business_time_field:
        properties["start_date"] = {
            "type": "string",
            "format": "date",
            "description": f"统计开始日期，格式 YYYY-MM-DD，包含当天；按 {business_time_field} 过滤",
        }
        properties["end_date"] = {
            "type": "string",
            "format": "date",
            "description": f"统计结束日期，格式 YYYY-MM-DD，包含当天；按 {business_time_field} 过滤",
        }
        if is_business_detail:
            required.extend(["start_date", "end_date"])
    filterable_fields: list[str] = []
    for row in field_meta or []:
        name = str(row.get("field_name") or "").strip()
        label = str(row.get("field_label") or name).strip()
        standard = str(row.get("standard_field_name") or "").strip()
        if name:
            parts = [name]
            if label:
                parts.append(label)
            if standard:
                parts.append(standard)
            filterable_fields.append("/".join(parts))
    fields_text = f" 可过滤字段：{', '.join(filterable_fields)}" if filterable_fields else ""

    # 根据数据源补充特殊说明
    extra_notes = ""
    if source_key == "new_employee_info":
        extra_notes = " 人员查询通常需要在 filters 中传入 {\"fbm\": \"/衡阳镭目/采购部\"}，结果包含在职和离职人员。"
    elif source_key == "erp_asset_purchase_detail":
        extra_notes = " 固定资产业务员字段为 sal_no_pona。"
    elif source_key == "erp_subcontract_detail":
        extra_notes = " 托工制单人字段为 usr（对应接口文档 USR_NAME）。"
    elif source_key == "erp_other_expense_detail":
        extra_notes = " 其它支出只有费用总额，无采购数量与单价，直接按金额字段计入。"

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return {
        "name": f"query_{source_key}",
        "description": (
            f"查询 {source_name} 数据。支持关键词、分页、历史快照、字段级过滤"
            + (f"和按 {business_time_field} 的业务时间范围过滤。" if business_time_field else "。")
            + "必须把用户指定的公司、供应商、员工、工号、单号等条件放入 filters 参数，由 PostgreSQL 在完整数据集上先筛选，再分页返回。禁止只读取第一页后就在本地判断没有找到。"
            + extra_notes
            + fields_text
        ),
        "inputSchema": input_schema,
    }


def _handle_mcp_jsonrpc(
    source_key: str,
    body: dict[str, Any],
    raw_token: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    method = body.get("method")
    rpc_id = body.get("id")
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "datamid-mcp", "version": "1.0"},
            },
        }

    if method == "initialized":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {}}

    if method == "tools/list":
        conn = get_connection()
        try:
            _validate_mcp_token_with_audit(conn, raw_token, payload, source_key, request)
            datasource = get_datasource_detail(conn, source_key)
            if not datasource:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Datasource not found"}}
            all_columns = get_business_columns(conn, datasource)
            field_meta = list_field_meta(conn, datasource, all_columns)
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"tools": [_build_mcp_tool(source_key, datasource, field_meta)]},
            }
        finally:
            conn.close()

    if method == "tools/call":
        tool_name = params.get("name", "")
        expected_name = f"query_{source_key}"
        if tool_name != expected_name:
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}}
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        conn = get_connection()
        try:
            ip = resolve_client_ip(request)
            token_row = _validate_mcp_token_with_audit(conn, raw_token, payload, source_key, request)
            check_mcp_token_anomaly(conn, token_row, payload, source_key, ip)
            datasource = get_datasource_detail(conn, source_key)
            if not datasource:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Datasource not found"}}
            mcp_user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (payload.get("uid"),)).fetchone()
            if not mcp_user:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "MCP token user no longer exists"}}
            # MCP 调用已鉴权，强制返回真实数据（不允许演示模式）
            raw_filters = arguments.get("filters")
            applied_filters = raw_filters if isinstance(raw_filters, dict) else None
            requested_page_size = int(arguments.get("page_size", MCP_DEFAULT_PAGE_SIZE))
            page_size = max(1, min(requested_page_size, MCP_MAX_PAGE_SIZE))
            result = query_datasource_rows(
                conn,
                datasource,
                str(arguments.get("keyword", "")),
                max(1, int(arguments.get("page", 1))),
                page_size,
                allow_real_data=True,
                as_of=str(arguments.get("as_of", "")),
                start_time=str(arguments.get("start_time", "")),
                end_time=str(arguments.get("end_time", "")),
                start_date=str(arguments.get("start_date", "")),
                end_date=str(arguments.get("end_date", "")),
                filters=applied_filters,
                user=mcp_user,
            )
            # 安全兜底：确保 MCP 不会把演示数据返回给客户端
            if result.get("preview_only"):
                result["preview_only"] = False
                result["message"] = "ok"
            if token_row is not None:
                mark_mcp_token_used(conn, int(token_row["id"]), ip)
                conn.commit()
            record_audit_log(
                payload.get("username", "unknown"),
                "user",
                "mcp_tool_call",
                source_key,
                f"token_id={int(token_row['id']) if token_row else ''};jti={payload.get('jti') or ''};dept={payload.get('department') or ''};employee={payload.get('employee_no') or ''};keyword={str(arguments.get('keyword', '')).strip()};page={int(arguments.get('page', 1))};page_size={page_size};row_count={len(result.get('rows', []))};total_count={int(result.get('total', 0))};as_of={str(arguments.get('as_of', '')).strip()};start_time={result.get('start_time', '')};end_time={result.get('end_time', '')};business_time_field={result.get('business_time_field', '')};filters={json.dumps(applied_filters or {}, ensure_ascii=False)};search_fields={','.join(result.get('search_fields', []))};accessed_fields={','.join(result.get('columns', []))}",
                ip,
                user_id=int(payload.get("uid") or 0) or None,
                employee_no=payload.get("employee_no") or "",
                department=payload.get("department") or "",
                token_id=int(token_row["id"]) if token_row is not None else None,
                jti=payload.get("jti") or "",
                source_name=datasource["source_name"] or source_key,
                keyword=str(arguments.get("keyword", "")).strip(),
                as_of=str(arguments.get("as_of", "")).strip(),
                start_time=result.get("start_time", ""),
                end_time=result.get("end_time", ""),
                business_time_field=result.get("business_time_field", ""),
                page=max(1, int(arguments.get("page", 1))),
                page_size=page_size,
                row_count=len(result.get("rows", [])),
                total_count=int(result.get("total") or 0),
                search_fields=",".join(result.get("search_fields", [])),
                accessed_fields=",".join(result.get("columns", [])),
            )
            text_result = json.dumps(_make_json_serializable(result), ensure_ascii=False, indent=2)
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": text_result}],
                    "isError": False,
                },
            }
        except HTTPException as exc:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": f"Datasource error: {exc.detail}"},
            }
        except Exception as exc:
            import traceback
            detail = f"{type(exc).__name__}: {exc}"
            traceback_str = traceback.format_exc()
            # 记录详细错误到后端日志，方便排查
            print(f"[MCP tools/call error] source_key={source_key}\n{traceback_str}")
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": f"Internal error: {detail}"},
            }
        finally:
            conn.close()

    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


@router.api_route("/api/mcp/data/{source_key}", methods=["GET", "POST"])
def api_mcp_data(
    source_key: str,
    request: Request,
    body: dict[str, Any] | None = None,
    keyword: str = "",
    as_of: str = "",
    start_time: str = "",
    end_time: str = "",
    start_date: str = "",
    end_date: str = "",
    filters: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(MCP_DEFAULT_PAGE_SIZE, ge=1, le=MCP_MAX_PAGE_SIZE),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """兼容旧的 MCP JSON-RPC over HTTP 端点（GET 普通查询 / POST JSON-RPC）。"""
    raw_token, payload = _authenticate_mcp_token(credentials, source_key, request)
    if request.method == "POST":
        if body is None:
            raise HTTPException(status_code=400, detail="Missing JSON-RPC body")
        return _handle_mcp_jsonrpc(source_key, body, raw_token, payload, request)

    # GET 保持原有普通查询行为
    ip = resolve_client_ip(request)
    parsed_filters = None
    if filters:
        try:
            parsed = json.loads(filters)
            parsed_filters = parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid filters JSON")
    effective_page_size = max(1, min(page_size, MCP_MAX_PAGE_SIZE))
    conn = get_connection()
    try:
        token_row = _validate_mcp_token_with_audit(conn, raw_token, payload, source_key, request)
        check_mcp_token_anomaly(conn, token_row, payload, source_key, ip)
        datasource = get_datasource_detail(conn, source_key)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        mcp_user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (payload.get("uid"),)).fetchone()
        if not mcp_user:
            raise HTTPException(status_code=401, detail="MCP token user no longer exists")
        # MCP 调用已鉴权，强制返回真实数据（不允许演示模式）
        result = query_datasource_rows(
            conn,
            datasource,
            keyword,
            page,
            effective_page_size,
            allow_real_data=True,
            as_of=as_of,
            start_time=start_time,
            end_time=end_time,
            start_date=start_date,
            end_date=end_date,
            filters=parsed_filters,
            user=mcp_user,
        )
        # 安全兜底：确保 MCP 不会把演示数据返回给客户端
        if result.get("preview_only"):
            result["preview_only"] = False
            result["message"] = "ok"
        if token_row is not None:
            mark_mcp_token_used(conn, int(token_row["id"]), ip)
            conn.commit()
        record_audit_log(
            payload.get("username", "unknown"),
            "user",
            "mcp_query",
            source_key,
            f"dept={payload.get('department') or ''} employee={payload.get('employee_no') or ''} ip={ip} keyword={keyword.strip() or ''} as_of={as_of.strip()} start_time={result.get('start_time', '')} end_time={result.get('end_time', '')} business_time_field={result.get('business_time_field', '')} search_fields={','.join(result.get('search_fields', []))}",
            ip,
            user_id=int(payload.get("uid") or 0) or None,
            employee_no=payload.get("employee_no") or "",
            department=payload.get("department") or "",
            token_id=int(token_row["id"]) if token_row is not None else None,
            jti=payload.get("jti") or "",
            source_name=datasource["source_name"] or source_key,
            keyword=keyword.strip(),
            as_of=as_of.strip(),
            start_time=result.get("start_time", ""),
            end_time=result.get("end_time", ""),
            business_time_field=result.get("business_time_field", ""),
            page=page,
            page_size=page_size,
            row_count=len(result.get("rows", [])),
            total_count=int(result.get("total") or 0),
            search_fields=",".join(result.get("search_fields", [])),
            accessed_fields=",".join(result.get("columns", [])),
        )
        return _make_json_serializable(result)
    finally:
        conn.close()


# ======== MCP over SSE ========
# 标准 MCP SSE 传输：
# 1. 客户端通过 Authorization 请求头 GET /api/mcp/sse/{source_key}
# 2. 服务端返回 SSE 流，首先发送 endpoint 事件
# 3. 客户端 POST JSON-RPC 到该 endpoint
# 4. 服务端通过 SSE 流返回响应

_SSE_SESSIONS: dict[str, dict[str, Any]] = {}
_SSE_LOCK = threading.Lock()
_SSE_SESSION_TTL_SECONDS = 300
_SSE_HEARTBEAT_SECONDS = 30


def _authenticate_mcp_token_header(request: Request, source_key: str) -> tuple[str, dict[str, Any]]:
    """Authenticate MCP with Authorization header or URL query parameter (legacy)."""
    raw_token = _extract_mcp_token(request)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing MCP token")
    payload = decode_mcp_token(raw_token)
    if source_key not in payload.get("source_keys", []):
        raise HTTPException(status_code=403, detail="MCP token has no access to this datasource")
    return raw_token, payload


def _create_sse_session(source_key: str, raw_token: str, payload: dict[str, Any]) -> tuple[str, queue.Queue]:
    session_id = uuid.uuid4().hex
    q: queue.Queue = queue.Queue()
    with _SSE_LOCK:
        _SSE_SESSIONS[session_id] = {
            "source_key": source_key,
            "raw_token": raw_token,
            "payload": payload,
            "queue": q,
            "created_at": time.time(),
        }
    return session_id, q


def _get_sse_session(session_id: str) -> dict[str, Any] | None:
    with _SSE_LOCK:
        sess = _SSE_SESSIONS.get(session_id)
        if sess is None:
            return None
        sess["last_active"] = time.time()
        return sess


def _close_sse_session(session_id: str) -> None:
    with _SSE_LOCK:
        _SSE_SESSIONS.pop(session_id, None)


def _touch_sse_session(session_id: str) -> None:
    with _SSE_LOCK:
        sess = _SSE_SESSIONS.get(session_id)
        if sess is not None:
            sess["last_active"] = time.time()


def _sse_event(event: str | None, data: Any) -> str:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if isinstance(data, str):
        lines.append(f"data: {data}")
    else:
        lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _sse_heartbeat() -> str:
    return ": keep-alive\n\n"


def _sse_cleanup_loop() -> None:
    while True:
        time.sleep(60)
        now = time.time()
        with _SSE_LOCK:
            expired = [
                sid
                for sid, sess in _SSE_SESSIONS.items()
                if now - sess.get("last_active", sess["created_at"]) > _SSE_SESSION_TTL_SECONDS
            ]
            for sid in expired:
                _SSE_SESSIONS.pop(sid, None)


_cleanup_thread = threading.Thread(target=_sse_cleanup_loop, daemon=True)
_cleanup_thread.start()


@router.get("/api/mcp/sse/{source_key}")
def api_mcp_sse(
    source_key: str,
    request: Request,
) -> StreamingResponse:
    """MCP SSE 传输端点。"""
    raw_token, payload = _authenticate_mcp_token_header(request, source_key)
    conn = get_connection()
    try:
        _validate_mcp_token_with_audit(conn, raw_token, payload, source_key, request)
    finally:
        conn.close()
    session_id, q = _create_sse_session(source_key, raw_token, payload)

    endpoint_url = f"{PUBLIC_URL}/api/mcp/message/{source_key}?session_id={session_id}"

    def event_stream():
        try:
            # 发送 endpoint 事件：data 为 POST endpoint 的相对或绝对 URI
            yield _sse_event("endpoint", endpoint_url).encode("utf-8")
            while True:
                try:
                    msg = q.get(timeout=_SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    _touch_sse_session(session_id)
                    yield _sse_heartbeat().encode("utf-8")
                    continue
                if msg is None:
                    break
                _touch_sse_session(session_id)
                yield _sse_event("message", msg).encode("utf-8")
        finally:
            _close_sse_session(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/mcp/message/{source_key}")
def api_mcp_message(
    source_key: str,
    request: Request,
    body: dict[str, Any],
    session_id: str = Query(...),
) -> Response:
    """MCP SSE 消息接收端点。"""
    raw_token, payload = _authenticate_mcp_token_header(request, source_key)
    sess = _get_sse_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="SSE session not found or expired")
    if sess["source_key"] != source_key:
        raise HTTPException(status_code=403, detail="Session source key mismatch")
    if not hmac.compare_digest(raw_token, str(sess.get("raw_token") or "")):
        raise HTTPException(status_code=403, detail="MCP token does not match the SSE session")

    result = _handle_mcp_jsonrpc(source_key, body, raw_token, payload, request)
    sess["queue"].put(result)

    return Response(status_code=202)


def _build_api_doc_markdown(
    source_key: str,
    ds_info: dict[str, Any],
    field_meta: list[dict[str, Any]],
    *,
    public_url: str = "",
) -> str:
    """根据数据源信息生成 Markdown 接口文档。"""
    base_url = str(public_url or "").rstrip("/")
    mcp_url = f"{base_url}/api/mcp/sse/{source_key}" if base_url else f"/api/mcp/sse/{source_key}"
    config_json = json.dumps(
        {
            "mcpServers": {
                f"ramon-datamid-{source_key}": {
                    "url": mcp_url,
                    "headers": {"Authorization": "Bearer <your_mcp_token>"},
                    "description": f"Datamid MCP endpoint for {source_key}",
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    )

    lines: list[str] = []
    lines.append(f"# {ds_info.get('source_name', source_key)} 接口文档")
    lines.append("")
    lines.append("## 接口概览")
    lines.append("")
    lines.append(f"- **数据源标识**：`{source_key}`")
    lines.append(f"- **数据源名称**：{ds_info.get('source_name', '')}")
    lines.append(f"- **所属平台**：{ds_info.get('platform_name') or '—'}")
    lines.append(f"- **接口 URL**：`{ds_info.get('api_url') or '—'}`")
    lines.append(f"- **请求方式**：{ds_info.get('http_method') or 'GET'}")
    lines.append(f"- **数据表**：`{ds_info.get('table_name') or '—'}`")
    lines.append(f"- **描述**：{ds_info.get('description') or '—'}")
    lines.append(f"- **最近同步**：{ds_info.get('last_sync_at') or '—'}")
    lines.append(f"- **当前版本**：{ds_info.get('current_sync_version') or '—'}")
    business_time_field = str(ds_info.get("business_time_field") or "").strip()
    lines.append(f"- **业务时间字段**：`{business_time_field}`" if business_time_field else "- **业务时间字段**：未配置")
    lines.append("")

    lines.append("## MCP Servers 配置模板")
    lines.append("")
    lines.append("本文档不会自动签发或显示令牌。请通过「MCP 导出」显式签发，并将 `<your_mcp_token>` 替换为签发时仅展示一次的令牌。")
    lines.append("")
    lines.append("```json")
    lines.append(config_json)
    lines.append("```")
    lines.append("")

    lines.append("## 认证方式")
    lines.append("")
    lines.append("所有 MCP 请求都必须使用请求头认证；URL 查询参数中的令牌会被拒绝：")
    lines.append("")
    lines.append("```\nAuthorization: Bearer <your_mcp_token>\n```")
    lines.append("")

    lines.append("## 请求参数")
    lines.append("")
    lines.append("| 参数名 | 类型 | 必填 | 说明 |")
    lines.append("|---|---|---|---|")
    lines.append("| keyword | string | 否 | 搜索关键词，按配置的可搜索字段模糊查询 |")
    lines.append("| page | integer | 否 | 页码，默认 1 |")
    lines.append(f"| page_size | integer | 否 | 每页条数，默认 {MCP_DEFAULT_PAGE_SIZE}，最大 {MCP_MAX_PAGE_SIZE} |")
    lines.append("| as_of | string | 否 | 历史快照时间，格式 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS` |")
    if business_time_field:
        lines.append(f"| start_date | string | {'是' if source_key in BUSINESS_DETAIL_SOURCE_KEYS else '否'} | `{business_time_field}` 的开始日期，格式 `YYYY-MM-DD`，闭区间 |")
        lines.append(f"| end_date | string | {'是' if source_key in BUSINESS_DETAIL_SOURCE_KEYS else '否'} | `{business_time_field}` 的结束日期，格式 `YYYY-MM-DD`，闭区间 |")
    lines.append("")

    lines.append("## 响应字段")
    lines.append("")
    if field_meta:
        lines.append("| 字段名 | 中文名 | 数据类型 | 标准字段 | 业务域 | 说明 |")
        lines.append("|---|---|---|---|---|---|")
        for item in field_meta:
            field_name = str(item.get("field_name") or "")
            field_label = str(item.get("field_label") or field_name)
            data_type = str(item.get("data_type") or "text")
            standard_name = str(item.get("standard_field_name") or "")
            business_domain = str(item.get("business_domain") or "")
            definition = str(item.get("definition") or "")
            lines.append(f"| {field_name} | {field_label} | {data_type} | {standard_name} | {business_domain} | {definition} |")
    else:
        lines.append("暂无字段元数据。")
    lines.append("")

    searchable = ds_info.get("searchable_fields") or []
    lines.append("## 可搜索字段")
    lines.append("")
    if searchable:
        lines.append(", ".join(f"`{field}`" for field in searchable))
    else:
        lines.append("未配置可搜索字段。")
    lines.append("")

    auth_header = "Authorization: Bearer <your_mcp_token>"
    lines.append("## 请求示例")
    lines.append("")
    lines.append(f"```http\nPOST /api/mcp/message/{source_key}?session_id=<session_id>\nContent-Type: application/json\n{auth_header}\n")
    lines.append("\n{")
    lines.append('  "jsonrpc": "2.0",')
    lines.append('  "method": "tools/call",')
    lines.append('  "id": 1,')
    lines.append('  "params": {')
    lines.append('    "name": "query_' + source_key + '",')
    lines.append('    "arguments": {')
    lines.append('      "keyword": "示例关键词",')
    lines.append('      "page": 1,')
    if business_time_field:
        lines.append(f'      "page_size": {MCP_DEFAULT_PAGE_SIZE},')
        lines.append('      "start_date": "2026-07-01",')
        lines.append('      "end_date": "2026-07-13"')
    else:
        lines.append(f'      "page_size": {MCP_DEFAULT_PAGE_SIZE}')
    lines.append('    }')
    lines.append('  }')
    lines.append('}\n```')
    lines.append("")

    lines.append("## 响应示例")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "jsonrpc": "2.0",')
    lines.append('  "id": 1,')
    lines.append('  "result": {')
    lines.append('    "content": [')
    lines.append('      {')
    lines.append('        "type": "text",')
    lines.append('        "text": "{...查询结果 JSON...}"')
    lines.append('      }')
    lines.append('    ],')
    lines.append('    "isError": false')
    lines.append('  }')
    lines.append('}')
    lines.append("```")
    lines.append("")

    lines.append("## 注意事项")
    lines.append("")
    lines.append("- 接口文档中的字段以最近一次同步成功的版本为准。")
    lines.append("- 如需获取真实数据，请先在「MCP 导出」中申请并显式生成 MCP 令牌。")
    lines.append("- 令牌只在签发响应中展示一次；请妥善保管，不要写入 URL、日志或公共代码仓库。")
    lines.append("- 历史版本查询通过 `as_of` 参数指定时间戳实现。")
    if business_time_field:
        lines.append(f"- 业务时间范围通过 `start_date`、`end_date` 过滤字段 `{business_time_field}`，与 `as_of` 历史快照可组合使用。")
    if source_key == "new_employee_info":
        lines.append("- 人员查询通常需要在 filters 中传入 `{\"fbm\": \"/衡阳镭目/采购部\"}`，结果包含在职和离职人员。")
    if source_key == "erp_asset_purchase_detail":
        lines.append("- 固定资产业务员字段为 `sal_no_pona`。")
    if source_key == "erp_subcontract_detail":
        lines.append("- 托工制单人字段为 `usr`（对应接口文档 `USR_NAME`）。")
    if source_key == "erp_other_expense_detail":
        lines.append("- 其它支出只有费用总额，无采购数量与单价，直接按金额字段计入。")
    lines.append("")

    return "\n".join(lines)


@router.get("/api/mcp/doc/{source_key}")
def api_mcp_doc(
    source_key: str,
    request: Request,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """返回不含凭据的 Markdown 接口文档（供前端弹窗展示）。

    权限与 MCP 导出一致：管理员任意访问，普通用户需有该数据源权限。
    """
    client_ip = resolve_client_ip(request)
    conn = get_connection()
    try:
        datasource = get_datasource_detail(conn, source_key)
        if not datasource:
            raise HTTPException(status_code=404, detail="Datasource not found")
        if user["role"] != "admin" and not has_source_permission(conn, user, source_key):
            raise HTTPException(status_code=403, detail="No permission to access this datasource")

        ds_info = serialize_datasource(conn, datasource, user)
        field_meta = list_field_meta(conn, datasource)
        markdown = _build_api_doc_markdown(
            source_key,
            ds_info,
            field_meta,
            public_url=PUBLIC_URL,
        )

        record_audit_log(
            user["username"],
            user["role"],
            "view_api_doc",
            source_key,
            f"source_name={ds_info.get('source_name', '')};credential_issued=0",
            client_ip,
        )

        return {
            "source_key": source_key,
            "source_name": ds_info.get("source_name", ""),
            "credential_issued": False,
            "markdown": markdown,
        }
    finally:
        conn.close()
