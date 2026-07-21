#!/usr/bin/env python3
"""验证 MCP 人均采购额改造是否正确生效。

用法：
    cd "d:/Data Platform Deployment Package7.14"
    py backend/scripts/verify_mcp_changes.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 加载 .env
env_path = Path(__file__).resolve().parents[2] / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()

import psycopg2
from psycopg2.extras import RealDictCursor

from backend.app.api.routers.public_data import _build_mcp_tool
from backend.app.services.datasource_query_service import query_datasource_rows
from backend.app.services.datasource_service import get_datasource_detail


DB_URL = os.getenv(
    "DATAMID_DATABASE_URL",
    "postgresql://datamid_app:{}@127.0.0.1:5430/datamid".format(os.getenv("DATAMID_DB_PASSWORD", "")),
)

SOURCE_KEYS = [
    "new_employee_info",
    "erp_purchase_order_detail",
    "erp_asset_purchase_detail",
    "erp_subcontract_detail",
    "erp_other_expense_detail",
]


def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


def verify_tool_schemas():
    print("=" * 70)
    print("1. MCP tools/list Schema 验证")
    print("=" * 70)
    conn = get_conn()
    try:
        for source_key in SOURCE_KEYS:
            ds = get_datasource_detail(conn, source_key)
            if not ds:
                print(f"  {source_key}: 数据源不存在！")
                continue
            from backend.app.services.datasource_service import get_business_columns, list_field_meta

            all_columns = get_business_columns(conn, ds)
            field_meta = list_field_meta(conn, ds, all_columns)
            tool = _build_mcp_tool(source_key, ds, field_meta)
            schema = tool["inputSchema"]
            required = schema.get("required", [])
            props = schema.get("properties", {})
            has_start_date = "start_date" in props
            has_end_date = "end_date" in props
            is_business = source_key != "new_employee_info"
            ok = has_start_date and has_end_date
            if is_business:
                ok = ok and "start_date" in required and "end_date" in required
            status = "OK" if ok else "FAIL"
            print(f"  {status} {source_key}: start_date={has_start_date}, end_date={has_end_date}, required={required}")
    finally:
        conn.close()


def verify_queries():
    print("\n" + "=" * 70)
    print("2. 查询验证")
    print("=" * 70)
    conn = get_conn()
    try:
        # 人员过滤
        ds = get_datasource_detail(conn, "new_employee_info")
        if ds:
            result = query_datasource_rows(
                conn,
                ds,
                "",
                1,
                10,
                allow_real_data=True,
                filters={"fbm": "/衡阳镭目/采购部"},
                user={"id": 1, "role": "admin", "username": "admin", "employee_no": "10001", "department": ""},
            )
            print(f"  new_employee_info 采购部过滤: total={result['total']}, rows={len(result['rows'])}")
            if result["rows"]:
                print(f"    首条: {json.dumps(result['rows'][0], ensure_ascii=False)[:200]}")

        # ERP 采购单按日期
        ds = get_datasource_detail(conn, "erp_purchase_order_detail")
        if ds:
            result = query_datasource_rows(
                conn,
                ds,
                "",
                1,
                5,
                allow_real_data=True,
                start_date="2025-01-01",
                end_date="2025-01-01",
                user={"id": 1, "role": "admin", "username": "admin", "employee_no": "10001", "department": ""},
            )
            print(f"  erp_purchase_order_detail 2025-01-01: total={result['total']}, rows={len(result['rows'])}")
            if result["rows"]:
                print(f"    首条: {json.dumps(result['rows'][0], ensure_ascii=False)[:300]}")

        # 托工（无数据，验证不报错）
        ds = get_datasource_detail(conn, "erp_subcontract_detail")
        if ds:
            result = query_datasource_rows(
                conn,
                ds,
                "",
                1,
                5,
                allow_real_data=True,
                start_date="2025-01-01",
                end_date="2025-01-31",
                user={"id": 1, "role": "admin", "username": "admin", "employee_no": "10001", "department": ""},
            )
            print(f"  erp_subcontract_detail 2025-01: total={result['total']}, rows={len(result['rows'])}")

        # 测试必填校验
        ds = get_datasource_detail(conn, "erp_other_expense_detail")
        if ds:
            try:
                query_datasource_rows(
                    conn,
                    ds,
                    "",
                    1,
                    5,
                    allow_real_data=True,
                    user={"id": 1, "role": "admin", "username": "admin", "employee_no": "10001", "department": ""},
                )
                print("  erp_other_expense_detail 必填校验: FAIL（未传日期未报错）")
            except Exception as exc:
                print(f"  erp_other_expense_detail 必填校验: OK（{exc.detail if hasattr(exc, 'detail') else exc}）")

        # 测试跨度校验
        try:
            query_datasource_rows(
                conn,
                ds,
                "",
                1,
                5,
                allow_real_data=True,
                start_date="2024-01-01",
                end_date="2025-12-31",
                user={"id": 1, "role": "admin", "username": "admin", "employee_no": "10001", "department": ""},
            )
            print("  erp_other_expense_detail 跨度校验: FAIL")
        except Exception as exc:
            print(f"  erp_other_expense_detail 跨度校验: OK（{exc.detail if hasattr(exc, 'detail') else exc}）")
    finally:
        conn.close()


def verify_indexes():
    print("\n" + "=" * 70)
    print("3. 索引验证")
    print("=" * 70)
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT indexname, tablename
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname LIKE 'idx_ods_%_date_doc'
               OR indexname = 'idx_ods_employee_info_dept_no'
            ORDER BY tablename, indexname
            """
        )
        rows = cur.fetchall()
        for row in rows:
            print(f"  {row['tablename']}: {row['indexname']}")
        if not rows:
            print("  未找到改造相关索引")
    finally:
        conn.close()


if __name__ == "__main__":
    verify_tool_schemas()
    verify_queries()
    verify_indexes()
