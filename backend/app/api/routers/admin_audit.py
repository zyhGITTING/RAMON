from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection

router = APIRouter()

_AUDIT_ACTION_LABELS = {
    "view_data": "查看数据",
    "mcp_query": "MCP 数据查询",
    "mcp_tool_call": "MCP 工具调用",
    "mcp_export": "生成 MCP 令牌",
    "mcp_export_http": "生成 MCP HTTP 令牌",
    "create_user": "创建用户",
    "delete_user": "删除用户",
    "set_department": "设置部门",
    "reset_password": "重置密码",
    "set_user_permissions": "设置用户数据源权限",
    "set_department_permissions": "设置部门数据源权限",
    "set_user_field_permissions": "设置用户字段权限",
    "set_department_field_permissions": "设置部门字段权限",
    "set_field_restriction": "设置字段限制",
    "update_field_metadata": "维护字段标准",
    "create_platform": "创建平台",
    "update_platform": "更新平台",
    "delete_platform": "删除平台",
    "create_llm_service": "创建 LLM 服务",
    "update_llm_service": "更新 LLM 服务",
    "delete_llm_service": "删除 LLM 服务",
    "parse_doc": "解析数据源文档",
    "parse_datasource_doc": "解析数据源文档",
    "create_datasource": "创建数据源",
    "update_datasource": "更新数据源",
    "delete_datasource": "删除数据源",
    "rollback_datasource_snapshot": "回滚数据源快照",
    "trigger_sync": "手动触发同步",
    "create_mcp_export_request": "申请 MCP 导出",
    "handle_mcp_export_request": "审批 MCP 导出申请",
    "revoke_mcp_token": "吊销 MCP 令牌",
    "update_mcp_token_expiry": "修改 MCP 有效期",
    "revoke_mcp_token_self": "用户吊销 MCP 令牌",
    "delete_mcp_token_self": "用户删除 MCP 令牌",
    "change_password": "修改密码",
}


_AUDIT_ACTION_LABELS.update({
    "mcp_token_rejected": "MCP 令牌被拒绝",
    "mcp_anomaly_detected": "MCP 异常调用",
})


@router.api_route("/api/admin/audit-log", methods=["GET", "POST"])
def admin_audit_log(keyword: str = "", jti: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        where_parts: list[str] = []
        params: list[Any] = []
        kw = keyword.strip()
        if kw:
            where_parts.append("(username LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ? OR ip LIKE ?)")
            params.extend([f"%{kw}%"] * 5)
        jti_value = jti.strip()
        if jti_value:
            where_parts.append("jti = ?")
            params.append(jti_value)
        where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM sys_audit_log{where}", params).fetchone()["c"])
        rows = conn.execute(f"SELECT * FROM sys_audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["action_label"] = _AUDIT_ACTION_LABELS.get(item.get("action", ""), item.get("action", ""))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


@router.get("/api/admin/audit-log/export")
def admin_audit_export(keyword: str = "", jti: str = "", admin=Depends(require_admin)) -> Response:
    data = admin_audit_log(keyword=keyword, jti=jti, page=1, page_size=5000, admin=admin)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "created_at", "username", "role", "action", "action_label", "target",
            "business_time_field", "start_time", "end_time", "detail", "ip",
        ],
    )
    writer.writeheader()
    for item in data["items"]:
        writer.writerow({key: item.get(key, "") for key in writer.fieldnames})
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=audit-log.csv"})
