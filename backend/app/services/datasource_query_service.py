from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from backend.app.services.datasource_service import (
    _quote_identifier,
    business_time_sql_expression,
    build_field_labels_from_meta,
    get_business_columns,
    has_table_column,
    list_field_meta,
    parse_datasource_config,
    table_exists,
    visible_columns,
)
from backend.app.services.permission_service import get_restricted_field_map, resolve_field_access
from backend.app.services.sync_service import get_sync_version_row

SEARCH_HINTS = ("code", "name", "no", "po", "erp", "supplier", "material", "model", "dept", "dep")

MAX_FILTER_FIELDS = 10

# 必须按 start_date/end_date 查询的业务明细数据源
BUSINESS_DETAIL_SOURCE_KEYS = {
    "erp_purchase_order_detail",
    "erp_asset_purchase_detail",
    "erp_subcontract_detail",
    "erp_other_expense_detail",
}

# 业务明细查询最大跨度（天）
MAX_BUSINESS_DATE_SPAN_DAYS = 366


def _date_to_timestamp_ms(value: datetime) -> int:
    """将 Asia/Shanghai（UTC+8，无夏令时）的日期时间转为 13 位毫秒时间戳。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(value.timestamp() * 1000)


def _parse_date_string(raw: str) -> datetime:
    """严格解析 YYYY-MM-DD。"""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty date")
    return datetime.strptime(text, "%Y-%m-%d")


def build_business_time_filter_clause(
    field_name: str,
    date_format: str,
    start_date: str,
    end_date: str,
) -> tuple[str, list[Any]]:
    """根据 date_format 生成业务时间范围过滤 SQL 子句与参数。

    timestamp_ms: 将日期边界转为毫秒时间戳，按整数比较（结束日为下一天 00:00:00 的毫秒值，左闭右开）。
    date_string: 使用已有的 business_time_sql_expression 字符串比较（闭区间）。
    """
    if date_format == "timestamp_ms":
        params: list[Any] = []
        clauses: list[str] = []
        start_dt = _parse_date_string(start_date)
        end_dt = _parse_date_string(end_date) + timedelta(days=1)
        field = _quote_identifier(field_name)
        numeric_field = f"CASE WHEN {field} IS NOT NULL AND {field} != '' THEN {field}::bigint ELSE NULL END"
        clauses.append(f"({numeric_field} >= ?)")
        params.append(_date_to_timestamp_ms(start_dt))
        clauses.append(f"({numeric_field} < ?)")
        params.append(_date_to_timestamp_ms(end_dt))
        return " AND ".join(clauses), params

    # 默认按字符串日期处理（闭区间）
    time_expression = business_time_sql_expression(field_name)
    params = []
    clauses: list[str] = []
    if start_date:
        clauses.append(f"({time_expression}) >= ?")
        params.append(start_date + " 00:00:00")
    if end_date:
        clauses.append(f"({time_expression}) <= ?")
        params.append(end_date + " 23:59:59")
    return " AND ".join(clauses), params


def build_stable_order_by(
    stable_sort_fields: list[str],
    date_format: str,
    business_time_field: str,
) -> str:
    """生成稳定排序 ORDER BY 子句。timestamp_ms 字段用数值排序，其它按字符串排序，最后以 id DESC 作为 tie-breaker。"""
    parts: list[str] = []
    for field in stable_sort_fields:
        if not field:
            continue
        if field == business_time_field and date_format == "timestamp_ms":
            parts.append(f"CASE WHEN {_quote_identifier(field)} IS NOT NULL AND {_quote_identifier(field)} != '' THEN {_quote_identifier(field)}::bigint ELSE NULL END ASC")
        else:
            parts.append(f"{_quote_identifier(field)} ASC")
    parts.append("id DESC")
    return ", ".join(parts)


def _validate_business_date_span(start_date: str, end_date: str, source_key: str) -> None:
    """校验业务明细查询的日期必填与跨度。"""
    if source_key not in BUSINESS_DETAIL_SOURCE_KEYS:
        return
    start_text = str(start_date or "").strip()
    end_text = str(end_date or "").strip()
    if not start_text or not end_text:
        raise HTTPException(
            status_code=400,
            detail="start_date 和 end_date 必填，格式为 YYYY-MM-DD",
        )
    try:
        start_dt = _parse_date_string(start_text)
        end_dt = _parse_date_string(end_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="start_date 和 end_date 格式必须为 YYYY-MM-DD",
        ) from exc
    if start_dt > end_dt:
        raise HTTPException(
            status_code=400,
            detail="start_date 不得晚于 end_date",
        )
    if (end_dt - start_dt).days > MAX_BUSINESS_DATE_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"start_date 和 end_date 查询跨度不能超过 {MAX_BUSINESS_DATE_SPAN_DAYS} 天",
        )


def _build_filter_field_index(
    field_meta: list[dict[str, Any]],
    columns: set[str],
    field_access: dict[str, str],
) -> dict[str, str]:
    """Map field labels/standard names/database names to the actual database field name."""
    index: dict[str, str] = {}
    for row in field_meta:
        field_name = str(row.get("field_name") or "").strip()
        if not field_name or field_name not in columns:
            continue
        if field_access.get(field_name) != "plain":
            continue
        index[field_name.lower()] = field_name
        label = str(row.get("field_label") or "").strip()
        if label:
            index[label.lower()] = field_name
        standard_name = str(row.get("standard_field_name") or "").strip()
        if standard_name:
            index[standard_name.lower()] = field_name
    return index


def parse_filters(
    raw_filters: Any,
    field_meta: list[dict[str, Any]],
    columns: set[str],
    field_access: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse document-style filters object {"字段标签": "值"} into applied filter items.

    Returns (applied_filters, unknown_keys). Only plain (non-hidden, non-masked)
    fields can be used. Maximum MAX_FILTER_FIELDS conditions.
    """
    if not raw_filters:
        return [], []
    if not isinstance(raw_filters, dict):
        return [], []
    index = _build_filter_field_index(field_meta, columns, field_access)
    applied: list[dict[str, Any]] = []
    unknown: list[str] = []
    for key, value in raw_filters.items():
        key_str = str(key or "").strip()
        if not key_str:
            continue
        field_name = index.get(key_str.lower())
        if field_name:
            applied.append({
                "field": field_name,
                "original_key": key_str,
                "value": "" if value is None else str(value),
            })
        else:
            unknown.append(key_str)
        if len(applied) >= MAX_FILTER_FIELDS:
            break
    return applied, unknown


def _build_filter_clause(filter_item: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build a case-insensitive contains WHERE clause for one filter condition."""
    field = _quote_identifier(filter_item["field"])
    value = str(filter_item["value"])
    return f"{field} ILIKE ?", [f"%{value}%"]


def get_field_mask_rules(conn, source_key: str) -> dict[str, str]:
    restricted = get_restricted_field_map(conn, source_key)
    return {name: info.get("mask_rule", "") for name, info in restricted.items()}


def apply_mask(value: Any, mask_rule: str = "") -> str:
    """简单脱敏。mask_rule: 'last4' 保留后4位；其余全掩码。"""
    text = "" if value is None else str(value)
    if not text:
        return text
    if mask_rule == "last4":
        return ("*" * max(0, len(text) - 4)) + text[-4:]
    return "****"


def parse_as_of_datetime(raw: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty as_of")
    if len(text) == 10:
        return datetime.strptime(text + " 23:59:59", "%Y-%m-%d %H:%M:%S")
    normalized = text.replace("T", " ").replace("Z", "")
    if len(normalized) == 16:
        normalized += ":59"
    return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")


def normalize_business_time_boundary(raw: str, *, end_of_period: bool) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    normalized = text.replace("T", " ")
    try:
        if len(normalized) == 10:
            parsed = datetime.strptime(normalized, "%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d") + (" 23:59:59" if end_of_period else " 00:00:00")
        if len(normalized) == 16:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M")
            return parsed.strftime("%Y-%m-%d %H:%M") + (":59" if end_of_period else ":00")
        parsed = datetime.strptime(normalized[:19], "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid business time. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
        ) from exc


def resolve_sync_version_for_as_of(conn, source_key: str, as_of: str):
    try:
        cutoff = parse_as_of_datetime(as_of).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid as_of format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS") from exc
    return conn.execute(
        """
        SELECT *
        FROM sys_sync_version
        WHERE source_key = ?
          AND status IN ('success', 'warning', 'empty')
          AND finished_at <= ?
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """,
        (source_key, cutoff),
    ).fetchone()


def list_sync_versions(conn, source_key: str, limit: int = 50):
    return conn.execute(
        """
        SELECT *
        FROM sys_sync_version
        WHERE source_key = ?
        ORDER BY finished_at DESC, id DESC
        LIMIT ?
        """,
        (source_key, limit),
    ).fetchall()


def resolve_searchable_fields(conn, datasource, columns: list[str]) -> list[str]:
    meta_rows = conn.execute(
        "SELECT field_name FROM sys_field_meta WHERE source_key = ? AND is_active = 1 AND is_searchable = 1 ORDER BY id",
        (datasource["source_key"],),
    ).fetchall()
    meta_fields = [str(row["field_name"]) for row in meta_rows if str(row["field_name"]) in columns]
    if meta_fields:
        return meta_fields
    config = parse_datasource_config(datasource)
    configured = config.get("searchable_fields") if isinstance(config.get("searchable_fields"), list) else []
    clean = [column for column in configured if column in columns]
    if clean:
        return clean
    return [column for column in columns if any(token in column.lower() for token in SEARCH_HINTS)]


def build_preview_rows(columns: list[str], field_labels: dict[str, Any], count: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(count):
        row: dict[str, Any] = {}
        for column in columns:
            label = str(field_labels.get(column, column))
            if any(token in label for token in ("数量", "金额", "天数", "缺口", "库存")):
                row[column] = str((idx + 1) * 10)
            elif "日期" in label:
                row[column] = (datetime.now() + timedelta(days=idx)).strftime("%Y-%m-%d")
            else:
                row[column] = f"{label}示例{idx + 1}"
        rows.append(row)
    return rows


def query_datasource_rows(
    conn,
    datasource,
    keyword: str,
    page: int,
    page_size: int,
    allow_real_data: bool,
    *,
    as_of: str = "",
    sync_version: str = "",
    start_time: str = "",
    end_time: str = "",
    start_date: str = "",
    end_date: str = "",
    filters: list[dict[str, Any]] | None = None,
    user=None,
) -> dict[str, Any]:
    source_key = str(datasource.get("source_key") or "").strip()
    all_columns = get_business_columns(conn, datasource)
    field_access = resolve_field_access(conn, user, source_key, all_columns)
    columns = visible_columns(field_access, all_columns)
    mask_rules = get_field_mask_rules(conn, source_key)
    field_meta = list_field_meta(conn, datasource, columns)
    field_labels = build_field_labels_from_meta(field_meta)
    config = parse_datasource_config(datasource)
    business_time_field = str(config.get("business_time_field") or "").strip()
    date_format = str(config.get("date_format") or "").strip() or "date_string"
    stable_sort_fields = config.get("stable_sort_fields") if isinstance(config.get("stable_sort_fields"), list) else []

    # 优先使用新的 start_date/end_date，兼容旧参数 start_time/end_time
    effective_start = str(start_date or start_time or "").strip()
    effective_end = str(end_date or end_time or "").strip()

    # 业务明细数据源必须传日期范围，并校验跨度
    _validate_business_date_span(effective_start, effective_end, source_key)

    normalized_start_time = ""
    normalized_end_time = ""
    if effective_start or effective_end:
        if not business_time_field:
            raise HTTPException(status_code=400, detail="Datasource has no business_time_field configured")
        if business_time_field not in all_columns:
            raise HTTPException(status_code=400, detail=f"Business time field is not available: {business_time_field}")
        if date_format == "timestamp_ms":
            # 毫秒时间戳字段直接用原始 YYYY-MM-DD 边界参与 SQL 转换
            normalized_start_time = effective_start
            normalized_end_time = effective_end
        else:
            normalized_start_time = normalize_business_time_boundary(effective_start, end_of_period=False)
            normalized_end_time = normalize_business_time_boundary(effective_end, end_of_period=True)
        if normalized_start_time and normalized_end_time and normalized_start_time > normalized_end_time:
            raise HTTPException(status_code=400, detail="start_date must be earlier than or equal to end_date")

    search_fields = [f for f in resolve_searchable_fields(conn, datasource, columns) if field_access.get(f) == "plain"]
    selected_version = None
    selected_as_of = as_of.strip()
    requested_sync_version = sync_version.strip()
    if requested_sync_version:
        selected_version = get_sync_version_row(conn, source_key, requested_sync_version)
        if not selected_version:
            raise HTTPException(status_code=404, detail="Snapshot version not found")
    elif selected_as_of:
        selected_version = resolve_sync_version_for_as_of(conn, source_key, selected_as_of)
        if not selected_version:
            raise HTTPException(status_code=404, detail="No snapshot exists before the specified as_of time")
    if not allow_real_data:
        return {
            "source_key": source_key,
            "source_name": datasource["source_name"],
            "columns": columns,
            "rows": build_preview_rows(columns, field_labels),
            "total": 6,
            "page": 1,
            "page_size": 6,
            "total_pages": 1,
            "has_more": False,
            "next_page": None,
            "chart": [],
            "preview_only": True,
            "field_labels": field_labels,
            "field_meta": field_meta,
            "search_fields": search_fields,
            "last_sync_at": datasource["last_sync_at"],
            "effective_sync_version": selected_version["sync_version"] if selected_version else "",
            "as_of": selected_as_of,
            "business_time_field": business_time_field,
            "start_time": normalized_start_time,
            "end_time": normalized_end_time,
            "start_date": effective_start,
            "end_date": effective_end,
            "applied_filters": {},
            "query_guidance": "Preview mode with demo rows",
            "message": "Preview mode with demo rows",
        }
    if not table_exists(conn, datasource["table_name"]):
        return {
            "source_key": source_key,
            "source_name": datasource["source_name"],
            "columns": columns,
            "rows": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
            "has_more": False,
            "next_page": None,
            "chart": [],
            "preview_only": False,
            "field_labels": field_labels,
            "field_meta": field_meta,
            "search_fields": search_fields,
            "last_sync_at": datasource["last_sync_at"],
            "effective_sync_version": selected_version["sync_version"] if selected_version else "",
            "as_of": selected_as_of,
            "business_time_field": business_time_field,
            "start_time": normalized_start_time,
            "end_time": normalized_end_time,
            "start_date": effective_start,
            "end_date": effective_end,
            "applied_filters": {},
            "query_guidance": "No synced data yet",
            "message": "No synced data yet",
        }
    where_clauses: list[str] = []
    params: list[Any] = []
    if selected_version:
        where_clauses.append("sync_version = ?")
        params.append(selected_version["sync_version"])
    elif has_table_column(conn, datasource["table_name"], "is_current"):
        where_clauses.append("is_current = 1")
    keyword = keyword.strip()
    if keyword and search_fields:
        where_clauses.append("(" + " OR ".join(f"{_quote_identifier(field)} ILIKE ?" for field in search_fields) + ")")
        params.extend([f"%{keyword}%"] * len(search_fields))
    if normalized_start_time or normalized_end_time:
        date_clause, date_params = build_business_time_filter_clause(
            business_time_field,
            date_format,
            normalized_start_time,
            normalized_end_time,
        )
        if date_clause:
            where_clauses.append(f"({date_clause})")
            params.extend(date_params)
    applied_filters, unknown_filters = parse_filters(filters, field_meta, set(columns), field_access)
    if unknown_filters:
        available_fields = []
        for row in field_meta:
            name = str(row.get("field_name") or "").strip()
            label = str(row.get("field_label") or "").strip()
            standard = str(row.get("standard_field_name") or "").strip()
            if name and field_access.get(name) == "plain":
                parts = [name]
                if label:
                    parts.append(label)
                if standard:
                    parts.append(standard)
                available_fields.append("/".join(parts))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown filter fields: {', '.join(unknown_filters)}. Available fields: {', '.join(available_fields)}",
        )
    for filter_item in applied_filters:
        clause, filter_params = _build_filter_clause(filter_item)
        if clause:
            where_clauses.append(clause)
            params.extend(filter_params)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM {_quote_identifier(datasource['table_name'])}{where_sql}", params).fetchone()["total"])
    offset = (page - 1) * page_size
    order_by = build_stable_order_by(stable_sort_fields, date_format, business_time_field)
    rows = conn.execute(
        f"SELECT * FROM {_quote_identifier(datasource['table_name'])}{where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()
    real_rows = []
    for row in rows:
        item: dict[str, Any] = {}
        for column in columns:
            if field_access.get(column) == "mask":
                item[column] = apply_mask(row[column], mask_rules.get(column, ""))
            else:
                item[column] = row[column]
        real_rows.append(item)
    chart_field = config.get("chart_field", "")
    chart: list[dict[str, Any]] = []
    if chart_field in columns and field_access.get(chart_field) == "plain":
        chart_where_sql = where_sql
        chart_params: list[Any] = list(params)
        chart = [
            {"name": row["label"] or "Unclassified", "value": int(row["value"])}
            for row in conn.execute(
                f"SELECT {_quote_identifier(chart_field)} AS label, COUNT(*) AS value FROM {_quote_identifier(datasource['table_name'])}{chart_where_sql} GROUP BY {_quote_identifier(chart_field)} ORDER BY value DESC, label ASC LIMIT 20",
                chart_params,
            ).fetchall()
        ]
    total_pages = math.ceil(total / page_size) if total > 0 else 1
    has_more = page < total_pages
    next_page = page + 1 if has_more else None
    query_guidance = "Results are filtered server-side across the full dataset."
    if has_more:
        query_guidance += f" Page {page} of {total_pages}; use next_page={next_page} for more results."
    return {
        "source_key": source_key,
        "source_name": datasource["source_name"],
        "columns": columns,
        "rows": real_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_more": has_more,
        "next_page": next_page,
        "chart": chart,
        "preview_only": False,
        "field_labels": field_labels,
        "field_meta": field_meta,
        "search_fields": search_fields,
        "last_sync_at": datasource["last_sync_at"],
        "effective_sync_version": selected_version["sync_version"] if selected_version else "",
        "effective_finished_at": selected_version["finished_at"] if selected_version else datasource["last_sync_at"],
        "as_of": selected_as_of,
        "business_time_field": business_time_field,
        "start_time": normalized_start_time,
        "end_time": normalized_end_time,
        "start_date": effective_start,
        "end_date": effective_end,
        "applied_filters": {item["original_key"]: item["value"] for item in applied_filters},
        "query_guidance": query_guidance,
        "message": "ok",
    }
