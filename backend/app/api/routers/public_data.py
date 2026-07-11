from __future__ import annotations

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
from backend.app.core.config import APP_PORT
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.services.auth_service import resolve_client_ip, security
from backend.app.services.datasource_query_service import query_datasource_rows
from backend.app.services.datasource_service import get_datasource_detail, list_field_meta, serialize_datasource
from backend.app.services.mcp_service import decode_mcp_token, issue_mcp_token, mark_mcp_token_used, validate_mcp_token_record
from backend.app.services.permission_service import has_source_permission

router = APIRouter()

PUBLIC_URL = os.getenv("DATAMID_PUBLIC_URL", f"http://localhost:{APP_PORT}").rstrip("/")


@router.get("/api/data/{source_key}")
def api_data(
    source_key: str,
    request: Request,
    keyword: str = "",
    as_of: str = "",
    sync_version: str = "",
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
            user=user,
        )
        record_audit_log(
            user["username"],
            user["role"],
            "view_data",
            source_key,
            f"keyword={keyword.strip() or ''};preview_only={int(result['preview_only'])};as_of={as_of.strip()};sync_version={sync_version.strip()}",
            resolve_client_ip(request),
            user_id=int(user["id"]),
            employee_no=user["employee_no"] or "",
            department=user["department"] or "",
            source_name=datasource["source_name"] or source_key,
            keyword=keyword.strip(),
            as_of=as_of.strip(),
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


def _authenticate_mcp_token(
    credentials: HTTPAuthorizationCredentials | None,
    source_key: str,
    request: Request,
) -> tuple[str, dict[str, Any]]:
    raw_token = None
    if credentials is not None:
        raw_token = credentials.credentials
    if not raw_token:
        raw_token = request.query_params.get("mcp_token") or request.query_params.get("token")
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


def _build_mcp_tool(source_key: str, datasource: Any) -> dict[str, Any]:
    source_name = datasource["source_name"] or source_key
    return {
        "name": f"query_{source_key}",
        "description": f"查询 {source_name} 数据。支持按关键词模糊搜索品号、品名等字段，返回分页数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词，支持品号、品名等模糊查询"},
                "page": {"type": "integer", "description": "页码，默认 1"},
                "page_size": {"type": "integer", "description": "每页条数，默认 20，最大 200"},
                "as_of": {"type": "string", "description": "历史版本时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS（可选）"},
            },
        },
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
            datasource = get_datasource_detail(conn, source_key)
            if not datasource:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Datasource not found"}}
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"tools": [_build_mcp_tool(source_key, datasource)]},
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
            token_row = validate_mcp_token_record(conn, raw_token, payload)
            datasource = get_datasource_detail(conn, source_key)
            if not datasource:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Datasource not found"}}
            mcp_user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (payload.get("uid"),)).fetchone()
            if not mcp_user:
                return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "MCP token user no longer exists"}}
            # MCP 调用已鉴权，强制返回真实数据（不允许演示模式）
            result = query_datasource_rows(
                conn,
                datasource,
                str(arguments.get("keyword", "")),
                max(1, int(arguments.get("page", 1))),
                max(1, min(int(arguments.get("page_size", 20)), 200)),
                allow_real_data=True,
                as_of=str(arguments.get("as_of", "")),
                user=mcp_user,
            )
            # 安全兜底：确保 MCP 不会把演示数据返回给客户端
            if result.get("preview_only"):
                result["preview_only"] = False
                result["message"] = "ok"
            ip = resolve_client_ip(request)
            if token_row is not None:
                mark_mcp_token_used(conn, int(token_row["id"]), ip)
                conn.commit()
            record_audit_log(
                payload.get("username", "unknown"),
                "user",
                "mcp_tool_call",
                source_key,
                f"token_id={int(token_row['id']) if token_row else ''};jti={payload.get('jti') or ''};dept={payload.get('department') or ''};employee={payload.get('employee_no') or ''};keyword={str(arguments.get('keyword', '')).strip()};page={int(arguments.get('page', 1))};page_size={max(1, min(int(arguments.get('page_size', 20)), 200))};row_count={len(result.get('rows', []))};total_count={int(result.get('total', 0))};as_of={str(arguments.get('as_of', '')).strip()};search_fields={','.join(result.get('search_fields', []))};accessed_fields={','.join(result.get('columns', []))}",
                ip,
                user_id=int(payload.get("uid") or 0) or None,
                employee_no=payload.get("employee_no") or "",
                department=payload.get("department") or "",
                token_id=int(token_row["id"]) if token_row is not None else None,
                jti=payload.get("jti") or "",
                source_name=datasource["source_name"] or source_key,
                keyword=str(arguments.get("keyword", "")).strip(),
                as_of=str(arguments.get("as_of", "")).strip(),
                page=max(1, int(arguments.get("page", 1))),
                page_size=max(1, min(int(arguments.get("page_size", 20)), 200)),
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
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
    conn = get_connection()
    try:
        token_row = validate_mcp_token_record(conn, raw_token, payload)
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
            page_size,
            allow_real_data=True,
            as_of=as_of,
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
            f"dept={payload.get('department') or ''} employee={payload.get('employee_no') or ''} ip={ip} keyword={keyword.strip() or ''} as_of={as_of.strip()} search_fields={','.join(result.get('search_fields', []))}",
            ip,
            user_id=int(payload.get("uid") or 0) or None,
            employee_no=payload.get("employee_no") or "",
            department=payload.get("department") or "",
            token_id=int(token_row["id"]) if token_row is not None else None,
            jti=payload.get("jti") or "",
            source_name=datasource["source_name"] or source_key,
            keyword=keyword.strip(),
            as_of=as_of.strip(),
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
# 1. 客户端 GET /api/mcp/sse/{source_key}?mcp_token=...
# 2. 服务端返回 SSE 流，首先发送 endpoint 事件
# 3. 客户端 POST JSON-RPC 到该 endpoint
# 4. 服务端通过 SSE 流返回响应

_SSE_SESSIONS: dict[str, dict[str, Any]] = {}
_SSE_LOCK = threading.Lock()
_SSE_SESSION_TTL_SECONDS = 300


def _authenticate_mcp_token_query(request: Request, source_key: str) -> tuple[str, dict[str, Any]]:
    """从 query param 或 header 提取并校验 MCP token。"""
    raw_token = None
    try:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            raw_token = auth[7:].strip()
    except Exception:
        pass
    if not raw_token:
        raw_token = request.query_params.get("mcp_token") or request.query_params.get("token")
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
    raw_token, payload = _authenticate_mcp_token_query(request, source_key)
    session_id, q = _create_sse_session(source_key, raw_token, payload)

    endpoint_url = f"{PUBLIC_URL}/api/mcp/message/{source_key}?session_id={session_id}&mcp_token={raw_token}"

    def event_stream():
        try:
            # 发送 endpoint 事件：data 为 POST endpoint 的相对或绝对 URI
            yield _sse_event("endpoint", endpoint_url).encode("utf-8")
            while True:
                try:
                    msg = q.get(timeout=_SSE_SESSION_TTL_SECONDS)
                except queue.Empty:
                    break
                if msg is None:
                    break
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
    raw_token, payload = _authenticate_mcp_token_query(request, source_key)
    sess = _get_sse_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="SSE session not found or expired")
    if sess["source_key"] != source_key:
        raise HTTPException(status_code=403, detail="Session source key mismatch")

    result = _handle_mcp_jsonrpc(source_key, body, raw_token, payload, request)
    sess["queue"].put(result)

    return Response(status_code=202)


def _build_api_doc_markdown(
    source_key: str,
    ds_info: dict[str, Any],
    field_meta: list[dict[str, Any]],
    *,
    mcp_token: str = "",
    public_url: str = "",
) -> str:
    """根据数据源信息生成 Markdown 接口文档。"""
    token = str(mcp_token or "").strip()
    base_url = str(public_url or "").rstrip("/")
    mcp_url = f"{base_url}/api/mcp/sse/{source_key}?mcp_token={token}" if token and base_url else ""
    config_json = ""
    if token and base_url:
        config_json = json.dumps(
            {
                "mcpServers": {
                    f"ramon-datamid-{source_key}": {
                        "url": mcp_url,
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
    lines.append("")

    if token:
        lines.append("## MCP 令牌（已内置）")
        lines.append("")
        lines.append("本接口文档已自动为你生成 MCP 令牌，可直接复制使用。")
        lines.append("")
        lines.append(f"- **MCP 调用 URL**：`{mcp_url}`")
        lines.append(f"- **令牌值**：`{token}`")
        lines.append("")
        lines.append("### MCP Servers 配置")
        lines.append("")
        lines.append("```json")
        lines.append(config_json)
        lines.append("```")
        lines.append("")

    lines.append("## 认证方式")
    lines.append("")
    if token:
        lines.append("本次已生成令牌，调用时直接使用下方示例中的 URL 或 Authorization 头即可。")
        lines.append("")
        lines.append("1. URL 查询参数：")
        lines.append(f"   ```\n   {mcp_url}\n   ```")
        lines.append("2. 请求头认证：")
        lines.append(f"   ```\n   Authorization: Bearer {token}\n   ```")
    else:
        lines.append("调用 MCP 数据查询接口时，可通过以下两种方式之一进行认证：")
        lines.append("")
        lines.append("1. 在 URL 查询参数中携带 `mcp_token`：")
        lines.append(f"   ```\n   /api/mcp/sse/{source_key}?mcp_token=<your_mcp_token>\n   ```")
        lines.append("2. 在请求头中提供：")
        lines.append("   ```\n   Authorization: Bearer <your_mcp_token>\n   ```")
    lines.append("")

    request_config = ds_info.get("request_config") or {}
    parameter_docs = request_config.get("parameter_docs") if isinstance(request_config.get("parameter_docs"), list) else []
    lines.append("## 请求参数")
    lines.append("")
    if parameter_docs:
        lines.append("| 参数名 | 中文名 | 类型 | 必填 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for item in parameter_docs:
            name = str(item.get("name") or "")
            label = str(item.get("label") or item.get("name") or "")
            param_type = str(item.get("type") or item.get("data_type") or "string")
            required = "是" if item.get("required") else "否"
            desc = str(item.get("description") or "")
            lines.append(f"| {name} | {label} | {param_type} | {required} | {desc} |")
    else:
        lines.append("| 参数名 | 类型 | 必填 | 说明 |")
        lines.append("|---|---|---|---|")
        lines.append("| keyword | string | 否 | 搜索关键词，支持品号、品名等模糊查询 |")
        lines.append("| page | integer | 否 | 页码，默认 1 |")
        lines.append("| page_size | integer | 否 | 每页条数，默认 20，最大 200 |")
        lines.append("| as_of | string | 否 | 历史版本时间，格式 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS` |")
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

    auth_header = f"Authorization: Bearer {token}" if token else "Authorization: Bearer <your_mcp_token>"
    lines.append("## 请求示例")
    lines.append("")
    lines.append(f"```http\nPOST /api/mcp/message/{source_key}?session_id=<session_id>&mcp_token=<your_mcp_token>\nContent-Type: application/json\n{auth_header}\n")
    lines.append("\n{")
    lines.append('  "jsonrpc": "2.0",')
    lines.append('  "method": "tools/call",')
    lines.append('  "id": 1,')
    lines.append('  "params": {')
    lines.append('    "name": "query_' + source_key + '",')
    lines.append('    "arguments": {')
    lines.append('      "keyword": "示例关键词",')
    lines.append('      "page": 1,')
    lines.append('      "page_size": 20')
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
    if token:
        lines.append("- 文档中的 MCP 令牌具有有效期，过期后请在「MCP 导出」中重新生成。")
        lines.append("- 请妥善保管令牌，不要将其提交到公共代码仓库或分享给无权限人员。")
    else:
        lines.append("- 如需获取真实数据，请先在「MCP 导出」中申请并生成 MCP 令牌。")
    lines.append("- 历史版本查询通过 `as_of` 参数指定时间戳实现。")
    lines.append("")

    return "\n".join(lines)


@router.get("/api/mcp/doc/{source_key}")
def api_mcp_doc(
    source_key: str,
    request: Request,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """返回带 MCP 令牌的 Markdown 接口文档（供前端弹窗展示）。

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

        # 自动生成 MCP 令牌并写入文档，与「MCP 导出」使用相同权限
        issued = issue_mcp_token(conn, user, [source_key], bind_ip=False, ip=client_ip)
        conn.commit()

        ds_info = serialize_datasource(conn, datasource, user)
        field_meta = list_field_meta(conn, datasource)
        markdown = _build_api_doc_markdown(
            source_key,
            ds_info,
            field_meta,
            mcp_token=issued["token"],
            public_url=PUBLIC_URL,
        )

        record_audit_log(
            user["username"],
            user["role"],
            "view_api_doc",
            source_key,
            f"source_name={ds_info.get('source_name', '')};token_id={issued.get('id', '')}",
            client_ip,
        )

        return {
            "source_key": source_key,
            "source_name": ds_info.get("source_name", ""),
            "mcp_token": issued["token"],
            "expires_at": issued["expires_at"],
            "markdown": markdown,
        }
    finally:
        conn.close()
