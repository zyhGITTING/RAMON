from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import uuid
from typing import Any

from fastapi import HTTPException

from backend.app.db.connection import get_connection
from backend.app.db.repositories.config import now_text
from backend.app.integrations.datasource_runtime import list_remote_rows
from backend.app.services.datasource_service import (
    get_datasource_detail,
    get_table_row_count,
    parse_datasource_config,
    prune_ods_table_versions,
    replace_ods_table_rows,
    sync_datasource_field_meta,
)
from backend.app.services.sync_progress_service import (
    clear_sync_cancel_request,
    finish_sync_progress,
    is_sync_cancel_requested,
    set_sync_running,
    start_sync_progress,
    update_sync_progress_item,
)


def _runtime_datasource(row: Any) -> dict[str, Any]:
    config = parse_datasource_config(row)
    return {
        "id": row["id"],
        "source_key": row["source_key"],
        "source_name": row["source_name"],
        "table_name": row["table_name"],
        "http_method": row["http_method"],
        "api_url": row["api_url"] or "",
        "verify_tls": bool(config.get("verify_tls", True)),
        "request_config": config.get("request", {}),
        "response_config": config.get("response", {}),
        "quality_rules": config.get("quality_rules", {}),
    }


def _evaluate_quality(row_count: int, previous_count: int, rules: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    change_rule = rules.get("row_count_change") if isinstance(rules, dict) and isinstance(rules.get("row_count_change"), dict) else {}
    warn_ratio = float(change_rule.get("warn_ratio", 0.8) or 0.8)
    ratio = None
    if previous_count > 0:
        ratio = row_count / previous_count

    if row_count == 0:
        summary = "No rows returned"
        return "empty", {"summary": summary, "row_count": row_count, "previous_row_count": previous_count, "ratio": ratio}, summary
    if ratio is not None and ratio < warn_ratio:
        summary = f"Row count dropped to {row_count} from {previous_count}"
        return "warning", {"summary": summary, "row_count": row_count, "previous_row_count": previous_count, "ratio": ratio}, summary
    summary = f"Synced {row_count} rows"
    return "success", {"summary": summary, "row_count": row_count, "previous_row_count": previous_count, "ratio": ratio}, summary


def _load_target_datasources(conn: Any, source_key: str | None) -> list[Any]:
    if source_key:
        row = get_datasource_detail(conn, source_key, include_disabled=False)
        if not row:
            raise HTTPException(status_code=404, detail="Datasource not found or disabled")
        return [row]
    return conn.execute(
        """
        SELECT *
        FROM sys_datasource
        WHERE enabled = 1
        ORDER BY COALESCE(platform_id, 9999), id
        """
    ).fetchall()


def _sync_one_datasource(row: Any, triggered_by: str) -> dict[str, Any]:
    source = row["source_key"]
    name = row["source_name"]
    started_at = now_text()
    started_perf = time.perf_counter()
    sync_version = ""
    failed_sync_version = ""
    update_sync_progress_item(
        source,
        source_name=name,
        status="syncing",
        fetched=0,
        total=None,
        page=0,
        row_count=0,
        message="Starting sync",
        quality_status="",
        quality_summary="",
    )
    try:
        with get_connection() as conn:
            live_row = get_datasource_detail(conn, source, include_disabled=False)
            if not live_row:
                raise HTTPException(status_code=404, detail="Datasource not found or disabled")
            previous_count = get_table_row_count(conn, live_row["table_name"])
            runtime_ds = _runtime_datasource(live_row)

            def _progress(page: int, batch_size: int, fetched: int, reported_total: int | None, has_next: bool | None) -> None:
                total_for_progress = reported_total if reported_total not in (None, 0) else max(fetched, batch_size)
                suffix = f"page {page}"
                if has_next is True:
                    suffix += " (more)"
                update_sync_progress_item(
                    source,
                    source_name=name,
                    status="syncing",
                    fetched=fetched,
                    total=total_for_progress,
                    page=page,
                    row_count=fetched,
                    message=f"Fetched {fetched} rows, {suffix}",
                    quality_status="",
                    quality_summary="",
                )

            rows, reported_total = list_remote_rows(
                runtime_ds,
                progress_callback=_progress,
                should_stop=is_sync_cancel_requested,
            )
            if is_sync_cancel_requested():
                raise RuntimeError("Sync cancelled")

            sync_batch_id = uuid.uuid4().hex
            sync_version = f"{started_at.replace('-', '').replace(':', '').replace(' ', '')}_{uuid.uuid4().hex[:8]}"
            replace_ods_table_rows(conn, live_row["table_name"], rows, sync_batch_id, sync_version)
            sync_datasource_field_meta(conn, live_row)
            row_count = len(rows)
            quality_status, quality_report, quality_summary = _evaluate_quality(
                row_count,
                previous_count,
                runtime_ds.get("quality_rules", {}),
            )
            finished_at = now_text()
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            conn.execute(
                """
                UPDATE sys_datasource
                SET last_sync_at = ?, last_status = ?, last_message = ?,
                    last_quality_status = ?, last_quality_report = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    quality_status,
                    quality_summary,
                    quality_status,
                    json.dumps(quality_report, ensure_ascii=False),
                    finished_at,
                    live_row["id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO sys_sync_log (
                    source_key, source_name, table_name, sync_version, status, message, row_count,
                    started_at, finished_at, duration_ms, triggered_by, quality_status, quality_report
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    live_row["source_key"],
                    live_row["source_name"],
                    live_row["table_name"],
                    sync_version,
                    quality_status,
                    quality_summary,
                    row_count,
                    started_at,
                    finished_at,
                    duration_ms,
                    triggered_by,
                    quality_status,
                    json.dumps(quality_report, ensure_ascii=False),
                ),
            )
            conn.execute("UPDATE sys_sync_version SET is_current = 0 WHERE source_key = ?", (live_row["source_key"],))
            conn.execute(
                """
                INSERT INTO sys_sync_version (
                    source_key, source_name, table_name, sync_version, sync_batch_id,
                    status, message, row_count, started_at, finished_at, duration_ms,
                    triggered_by, quality_status, quality_report, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    live_row["source_key"],
                    live_row["source_name"],
                    live_row["table_name"],
                    sync_version,
                    sync_batch_id,
                    quality_status,
                    quality_summary,
                    row_count,
                    started_at,
                    finished_at,
                    duration_ms,
                    triggered_by,
                    quality_status,
                    json.dumps(quality_report, ensure_ascii=False),
                ),
            )
            prune_ods_table_versions(conn, live_row["source_key"], live_row["table_name"])
            conn.commit()

        progress_status = quality_status if quality_status in {"warning", "empty"} else "success"
        update_sync_progress_item(
            source,
            source_name=name,
            status=progress_status,
            fetched=row_count,
            total=reported_total or row_count,
            page=1,
            row_count=row_count,
            message=quality_summary,
            quality_status=quality_status,
            quality_summary=quality_report.get("summary", ""),
            sync_version=sync_version,
        )
        return {
            "source_key": source,
            "source_name": name,
            "status": progress_status,
            "sync_version": sync_version,
            "row_count": row_count,
            "fetched": row_count,
            "total": reported_total or row_count,
            "message": quality_summary,
            "quality_status": quality_status,
            "quality_summary": quality_report.get("summary", ""),
            "_summary_key": quality_status,
        }
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        finished_at = now_text()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        with get_connection() as conn:
            live_row = get_datasource_detail(conn, source, include_disabled=True)
            if live_row:
                failed_sync_version = f"{started_at.replace('-', '').replace(':', '').replace(' ', '')}_{uuid.uuid4().hex[:8]}"
                conn.execute(
                    """
                    UPDATE sys_datasource
                    SET last_sync_at = ?, last_status = ?, last_message = ?,
                        last_quality_status = ?, last_quality_report = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        finished_at,
                        "failed",
                        detail,
                        "failed",
                        json.dumps({"summary": detail}, ensure_ascii=False),
                        finished_at,
                        live_row["id"],
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO sys_sync_log (
                        source_key, source_name, table_name, sync_version, status, message, row_count,
                        started_at, finished_at, duration_ms, triggered_by, quality_status, quality_report
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        live_row["source_key"],
                        live_row["source_name"],
                        live_row["table_name"],
                        failed_sync_version,
                        "failed",
                        detail,
                        0,
                        started_at,
                        finished_at,
                        duration_ms,
                        triggered_by,
                        "failed",
                        json.dumps({"summary": detail}, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO sys_sync_version (
                        source_key, source_name, table_name, sync_version, sync_batch_id,
                        status, message, row_count, started_at, finished_at, duration_ms,
                        triggered_by, quality_status, quality_report, is_current
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        live_row["source_key"],
                        live_row["source_name"],
                        live_row["table_name"],
                        failed_sync_version,
                        "",
                        "failed",
                        detail,
                        0,
                        started_at,
                        finished_at,
                        duration_ms,
                        triggered_by,
                        "failed",
                        json.dumps({"summary": detail}, ensure_ascii=False),
                    ),
                )
                conn.commit()
        update_sync_progress_item(
            source,
            source_name=name,
            status="failed",
            fetched=0,
            total=None,
            page=1,
            row_count=0,
            message=detail,
            quality_status="failed",
            quality_summary=detail,
            sync_version=failed_sync_version,
        )
        return {
            "source_key": source,
            "source_name": name,
            "status": "failed",
            "sync_version": failed_sync_version,
            "row_count": 0,
            "fetched": 0,
            "total": None,
            "message": detail,
            "quality_status": "failed",
            "quality_summary": detail,
            "_summary_key": "failed",
        }


def run_sync(source_key: str | None = None, triggered_by: str = "system") -> dict[str, Any]:
    with get_connection() as conn:
        datasources = _load_target_datasources(conn, source_key)

    set_sync_running(True)
    clear_sync_cancel_request()
    start_sync_progress(datasources)

    finished_items: list[dict[str, Any]] = []
    summary = {"success": 0, "empty": 0, "warning": 0, "failed": 0, "total": len(datasources)}
    result_error = ""

    try:
        max_workers = 1 if source_key else min(4, max(1, len(datasources)))
        order_map = {row["source_key"]: idx for idx, row in enumerate(datasources)}
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="datamid-sync-worker") as executor:
            futures = [executor.submit(_sync_one_datasource, row, triggered_by) for row in datasources]
            for future in as_completed(futures):
                item = future.result()
                summary_key = item.pop("_summary_key", "failed")
                summary[summary_key] = int(summary.get(summary_key, 0)) + 1
                finished_items.append(item)

        finished_items.sort(key=lambda item: order_map.get(item["source_key"], 999999))
        if is_sync_cancel_requested():
            result_error = "Sync cancelled"

        if result_error == "Sync cancelled":
            for row in datasources:
                source = row["source_key"]
                item = next((x for x in finished_items if x["source_key"] == source), None)
                if item:
                    continue
                update_sync_progress_item(
                    source,
                    source_name=row["source_name"],
                    status="failed",
                    message="Sync cancelled",
                    quality_status="failed",
                    quality_summary="Sync cancelled",
                )
                finished_items.append(
                    {
                        "source_key": source,
                        "source_name": row["source_name"],
                        "status": "failed",
                        "row_count": 0,
                        "fetched": 0,
                        "total": None,
                        "message": "Sync cancelled",
                        "quality_status": "failed",
                        "quality_summary": "Sync cancelled",
                    }
                )
                summary["failed"] += 1

        final_result = {
            "triggered_by": triggered_by,
            "summary": summary,
            "items": finished_items,
        }
        finish_sync_progress(summary=final_result, error=result_error)
        return {
            "message": "Sync finished" if not result_error else result_error,
            "summary": summary,
            "items": finished_items,
            "error": result_error,
        }
    finally:
        set_sync_running(False)
        clear_sync_cancel_request()
