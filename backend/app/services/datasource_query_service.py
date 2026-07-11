from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from backend.app.services.datasource_service import (
    _quote_identifier,
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
    user=None,
) -> dict[str, Any]:
    all_columns = get_business_columns(conn, datasource)
    field_access = resolve_field_access(conn, user, datasource["source_key"], all_columns)
    columns = visible_columns(field_access, all_columns)
    mask_rules = get_field_mask_rules(conn, datasource["source_key"])
    field_meta = list_field_meta(conn, datasource, columns)
    field_labels = build_field_labels_from_meta(field_meta)
    config = parse_datasource_config(datasource)
    search_fields = [f for f in resolve_searchable_fields(conn, datasource, columns) if field_access.get(f) == "plain"]
    selected_version = None
    selected_as_of = as_of.strip()
    requested_sync_version = sync_version.strip()
    if requested_sync_version:
        selected_version = get_sync_version_row(conn, datasource["source_key"], requested_sync_version)
        if not selected_version:
            raise HTTPException(status_code=404, detail="Snapshot version not found")
    elif selected_as_of:
        selected_version = resolve_sync_version_for_as_of(conn, datasource["source_key"], selected_as_of)
        if not selected_version:
            raise HTTPException(status_code=404, detail="No snapshot exists before the specified as_of time")
    if not allow_real_data:
        return {
            "source_key": datasource["source_key"],
            "source_name": datasource["source_name"],
            "columns": columns,
            "rows": build_preview_rows(columns, field_labels),
            "total": 6,
            "page": 1,
            "page_size": 6,
            "chart": [],
            "preview_only": True,
            "field_labels": field_labels,
            "field_meta": field_meta,
            "search_fields": search_fields,
            "last_sync_at": datasource["last_sync_at"],
            "effective_sync_version": selected_version["sync_version"] if selected_version else "",
            "as_of": selected_as_of,
            "message": "Preview mode with demo rows",
        }
    if not table_exists(conn, datasource["table_name"]):
        return {
            "source_key": datasource["source_key"],
            "source_name": datasource["source_name"],
            "columns": columns,
            "rows": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "chart": [],
            "preview_only": False,
            "field_labels": field_labels,
            "field_meta": field_meta,
            "search_fields": search_fields,
            "last_sync_at": datasource["last_sync_at"],
            "effective_sync_version": selected_version["sync_version"] if selected_version else "",
            "as_of": selected_as_of,
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
        where_clauses.append("(" + " OR ".join(f"{_quote_identifier(field)} LIKE ?" for field in search_fields) + ")")
        params.extend([f"%{keyword}%"] * len(search_fields))
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM {_quote_identifier(datasource['table_name'])}{where_sql}", params).fetchone()["total"])
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM {_quote_identifier(datasource['table_name'])}{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
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
        chart_where_sql = ""
        chart_params: list[Any] = []
        if selected_version:
            chart_where_sql = " WHERE sync_version = ?"
            chart_params.append(selected_version["sync_version"])
        elif has_table_column(conn, datasource["table_name"], "is_current"):
            chart_where_sql = " WHERE is_current = 1"
        chart = [
            {"name": row["label"] or "Unclassified", "value": int(row["value"])}
            for row in conn.execute(
                f"SELECT {_quote_identifier(chart_field)} AS label, COUNT(*) AS value FROM {_quote_identifier(datasource['table_name'])}{chart_where_sql} GROUP BY {_quote_identifier(chart_field)} ORDER BY value DESC, label ASC LIMIT 20",
                chart_params,
            ).fetchall()
        ]
    return {
        "source_key": datasource["source_key"],
        "source_name": datasource["source_name"],
        "columns": columns,
        "rows": real_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "chart": chart,
        "preview_only": False,
        "field_labels": field_labels,
        "field_meta": field_meta,
        "search_fields": search_fields,
        "last_sync_at": datasource["last_sync_at"],
        "effective_sync_version": selected_version["sync_version"] if selected_version else "",
        "effective_finished_at": selected_version["finished_at"] if selected_version else datasource["last_sync_at"],
        "as_of": selected_as_of,
        "message": "ok",
    }
