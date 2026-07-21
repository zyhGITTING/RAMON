from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import uuid
from typing import Any

from fastapi import HTTPException

from backend.app.db.connection import get_connection
from backend.app.db.repositories.config import now_text
from backend.app.db.repositories.sync_checkpoint import (
    create_checkpoint,
    get_checkpoint,
    is_checkpoint_stale,
    mark_checkpoint_completed,
    mark_checkpoint_failed,
    reset_checkpoint,
    update_checkpoint_progress,
)
from backend.app.integrations.datasource_runtime import DatasourcePayloadError, iter_remote_pages
from backend.app.services.datasource_service import (
    append_ods_staging_rows,
    compute_time_window_boundaries,
    create_ods_staging_table,
    discard_ods_staging_rows,
    ensure_staging_table,
    finalize_ods_staging_rows,
    get_datasource_detail,
    get_existing_staging_row_count,
    get_time_window_row_count,
    has_table_column,
    merge_time_window_staging_rows,
    parse_datasource_config,
    prune_ods_table_versions,
    prune_rejected_sync_versions,
    sync_datasource_field_meta,
    table_exists,
    truncate_staging_table,
)
from backend.app.services.sync_progress_service import (
    clear_sync_cancel_request,
    finish_sync_progress,
    is_sync_cancel_requested,
    set_sync_running,
    start_sync_progress,
    update_sync_progress_item,
)


_STALE_RESUME_HOURS = 24


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
        "business_time_field": config.get("business_time_field", ""),
        "date_format": config.get("date_format", ""),
        "incremental_config": config.get("incremental", {}),
    }


def _is_time_window_incremental(incremental_config: dict[str, Any] | None) -> bool:
    if not incremental_config:
        return False
    if not incremental_config.get("enabled"):
        return False
    return (
        str(incremental_config.get("strategy") or "").strip() == "date_range"
        and str(incremental_config.get("merge_strategy") or "").strip() == "time_window_replace"
    )


def _quality_ratio(value: Any, default: float) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    return ratio if 0 <= ratio <= 1 else default


def _evaluate_quality(
    row_count: int,
    previous_count: int,
    rules: dict[str, Any],
) -> tuple[str, dict[str, Any], str, bool]:
    clean_rules = rules if isinstance(rules, dict) else {}
    change_rule = clean_rules.get("row_count_change") if isinstance(clean_rules.get("row_count_change"), dict) else {}
    # Backward compatibility: the old warn_ratio becomes the publish gate unless
    # an administrator explicitly supplies reject_ratio/min_publish_ratio.
    reject_ratio = _quality_ratio(
        change_rule.get("reject_ratio", change_rule.get("min_publish_ratio", change_rule.get("warn_ratio", 0.8))),
        0.8,
    )
    warn_ratio = max(reject_ratio, _quality_ratio(change_rule.get("warn_ratio", reject_ratio), reject_ratio))
    allow_empty_publish = clean_rules.get("allow_empty_publish") is True
    ratio = None
    if previous_count > 0:
        ratio = row_count / previous_count

    report = {
        "row_count": row_count,
        "previous_row_count": previous_count,
        "ratio": ratio,
        "reject_ratio": reject_ratio,
        "warn_ratio": warn_ratio,
        "allow_empty_publish": allow_empty_publish,
    }
    if row_count == 0:
        if allow_empty_publish:
            summary = "Published an empty snapshot by explicit datasource policy"
            return "empty", {**report, "summary": summary, "published": True, "reason_code": "empty_allowed"}, summary, True
        summary = "Rejected empty snapshot; last-known-good remains current"
        return "rejected", {**report, "summary": summary, "published": False, "reason_code": "empty_snapshot"}, summary, False
    if ratio is not None and ratio < reject_ratio:
        summary = f"Rejected row-count drop to {row_count} from {previous_count}; last-known-good remains current"
        return "rejected", {**report, "summary": summary, "published": False, "reason_code": "row_count_drop"}, summary, False
    if ratio is not None and ratio < warn_ratio:
        summary = f"Published with warning: row count dropped to {row_count} from {previous_count}"
        return "warning", {**report, "summary": summary, "published": True, "reason_code": "row_count_warning"}, summary, True
    summary = f"Synced {row_count} rows"
    return "success", {**report, "summary": summary, "published": True, "reason_code": "accepted"}, summary, True


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


def _staging_table_name(table_name: str) -> str:
    return f"stg_{table_name}"


def _prepare_sync_batch(
    conn,
    live_row: Any,
    runtime_ds: dict[str, Any],
    triggered_by: str,
) -> tuple[str, str, str, int, int, dict[str, Any] | None]:
    """Return (sync_batch_id, sync_version, staging_name, start_page, resumed_row_count, window)."""
    source = runtime_ds["source_key"]
    table_name = live_row["table_name"]
    staging_name = _staging_table_name(table_name)
    incremental_config = runtime_ds.get("incremental_config") or {}
    strategy = "full" if not incremental_config.get("enabled") else str(incremental_config.get("strategy") or "full")
    is_time_window = _is_time_window_incremental(incremental_config)
    window: dict[str, Any] | None = None

    checkpoint = get_checkpoint(conn, source)
    if checkpoint and checkpoint.get("status") in {"running", "failed"} and not is_checkpoint_stale(checkpoint, _STALE_RESUME_HOURS):
        existing_batch = str(checkpoint.get("sync_batch_id") or "").strip()
        existing_version = str(checkpoint.get("sync_version") or "").strip()
        existing_staging = _staging_table_name(table_name)
        if existing_batch and existing_version and table_exists(conn, existing_staging):
            # Resume from the last successfully committed page.
            start_page = int(checkpoint.get("last_fetched_page") or 0) + 1
            resumed_row_count = int(checkpoint.get("last_fetched_row_count") or 0)
            # Verify the staging table really belongs to this batch.
            staged_count = get_existing_staging_row_count(conn, existing_staging, existing_batch)
            if staged_count > 0:
                ensure_staging_table(conn, table_name, existing_staging)
                if is_time_window:
                    window = compute_time_window_boundaries(incremental_config, checkpoint)
                create_checkpoint(
                    conn,
                    source,
                    existing_batch,
                    existing_version,
                    strategy=strategy,
                    start_date=window["start_date"] if window else "",
                    end_date=window["end_date"] if window else "",
                )
                return existing_batch, existing_version, existing_staging, start_page, resumed_row_count, window

    # Fresh start: reset checkpoint and truncate any leftover staging.
    reset_checkpoint(conn, source, strategy=strategy)
    if table_exists(conn, staging_name):
        truncate_staging_table(conn, staging_name)
    else:
        create_ods_staging_table(conn, table_name, staging_name, durable=True)

    started_at = now_text()
    sync_batch_id = uuid.uuid4().hex
    sync_version = f"{started_at.replace('-', '').replace(':', '').replace(' ', '')}_{uuid.uuid4().hex[:8]}"
    if is_time_window:
        window = compute_time_window_boundaries(incremental_config, checkpoint)
    create_checkpoint(
        conn,
        source,
        sync_batch_id,
        sync_version,
        strategy=strategy,
        start_date=window["start_date"] if window else "",
        end_date=window["end_date"] if window else "",
    )
    return sync_batch_id, sync_version, staging_name, 1, 0, window


def _sync_one_datasource(row: Any, triggered_by: str) -> dict[str, Any]:
    source = row["source_key"]
    name = row["source_name"]
    started_at = now_text()
    started_perf = time.perf_counter()
    sync_version = ""
    failed_sync_version = ""
    sync_batch_id = ""
    staging_name = ""
    window: dict[str, Any] | None = None
    strategy = "full"
    row_count = 0
    reported_total: int | None = None
    last_page = 0
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
            previous_version = conn.execute(
                """
                SELECT row_count
                FROM sys_sync_version
                WHERE source_key = ? AND is_current = 1
                ORDER BY finished_at DESC, id DESC
                LIMIT 1
                """,
                (source,),
            ).fetchone()
            previous_count = int(previous_version["row_count"] or 0) if previous_version else 0
            runtime_ds = _runtime_datasource(live_row)

            sync_batch_id, sync_version, staging_name, start_page, resumed_row_count, window = _prepare_sync_batch(
                conn,
                live_row,
                runtime_ds,
                triggered_by,
            )
            conn.commit()

            if window:
                # Make a mutable copy so we can attach the precomputed window.
                runtime_ds = {**runtime_ds}
                runtime_ds["incremental_config"] = {**runtime_ds.get("incremental_config", {}), **window}
                business_time_field = runtime_ds.get("business_time_field", "")
                if (
                    business_time_field
                    and table_exists(conn, live_row["table_name"])
                    and has_table_column(conn, live_row["table_name"], business_time_field)
                ):
                    previous_count = get_time_window_row_count(
                        conn,
                        live_row["table_name"],
                        business_time_field,
                        runtime_ds.get("date_format", ""),
                        window["start_date"],
                        window["end_date"],
                    )
                else:
                    previous_count = 0
            else:
                previous_count = int(previous_version["row_count"] or 0) if previous_version else 0

            incremental_config = runtime_ds.get("incremental_config") or {}
            strategy = str(incremental_config.get("strategy") or "full") if incremental_config.get("enabled") else "full"

            for page, rows, page_total, has_next in iter_remote_pages(
                runtime_ds,
                should_stop=is_sync_cancel_requested,
                start_page=start_page,
                sequential=True,
            ):
                if is_sync_cancel_requested():
                    raise RuntimeError("Sync cancelled")
                append_ods_staging_rows(
                    conn,
                    live_row["table_name"],
                    staging_name,
                    rows,
                    sync_batch_id,
                    sync_version,
                    runtime_ds.get("business_time_field", ""),
                    sync_page=page,
                )
                conn.commit()
                # Update progress after commit so checkpoint and staging are consistent.
                row_count += len(rows)
                update_checkpoint_progress(
                    conn,
                    source,
                    page=page,
                    row_count=resumed_row_count + row_count,
                )
                conn.commit()
                last_page = page
                if page_total is not None:
                    reported_total = page_total
                total_for_progress = reported_total if reported_total not in (None, 0) else max(resumed_row_count + row_count, len(rows))
                suffix = f"page {page}"
                if has_next is True:
                    suffix += " (more)"
                update_sync_progress_item(
                    source,
                    source_name=name,
                    status="syncing",
                    fetched=resumed_row_count + row_count,
                    total=total_for_progress,
                    page=page,
                    row_count=resumed_row_count + row_count,
                    message=f"Staged {resumed_row_count + row_count} rows, {suffix}",
                    quality_status="",
                    quality_summary="",
                )
            if is_sync_cancel_requested():
                raise RuntimeError("Sync cancelled")

            total_row_count = resumed_row_count + row_count
            # Candidate quality is decided before any current flag is changed.
            quality_status, quality_report, quality_summary, publish_candidate = _evaluate_quality(
                total_row_count,
                previous_count,
                runtime_ds.get("quality_rules", {}),
            )
            if publish_candidate:
                if window:
                    merge_time_window_staging_rows(
                        conn,
                        live_row["table_name"],
                        staging_name,
                        runtime_ds.get("business_time_field", ""),
                        runtime_ds.get("date_format", ""),
                        window["start_date"],
                        window["end_date"],
                    )
                else:
                    finalize_ods_staging_rows(conn, live_row["table_name"], staging_name)
                discard_ods_staging_rows(conn, staging_name)
                sync_datasource_field_meta(conn, live_row)
                mark_checkpoint_completed(conn, source)
                if window:
                    update_checkpoint_progress(
                        conn,
                        source,
                        page=last_page,
                        row_count=total_row_count,
                        watermark_value=window["end_date"],
                    )
            else:
                # Rejected candidate data stays in a temporary table only and is
                # deleted immediately. The existing current snapshot is untouched.
                discard_ods_staging_rows(conn, staging_name)
                reset_checkpoint(conn, source)
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
                    total_row_count,
                    started_at,
                    finished_at,
                    duration_ms,
                    triggered_by,
                    quality_status,
                    json.dumps(quality_report, ensure_ascii=False),
                ),
            )
            if publish_candidate:
                conn.execute("UPDATE sys_sync_version SET is_current = 0 WHERE source_key = ?", (live_row["source_key"],))
            conn.execute(
                """
                INSERT INTO sys_sync_version (
                    source_key, source_name, table_name, sync_version, sync_batch_id,
                    status, message, row_count, started_at, finished_at, duration_ms,
                    triggered_by, quality_status, quality_report, is_current, strategy, watermark_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    live_row["source_key"],
                    live_row["source_name"],
                    live_row["table_name"],
                    sync_version,
                    sync_batch_id,
                    quality_status,
                    quality_summary,
                    total_row_count,
                    started_at,
                    finished_at,
                    duration_ms,
                    triggered_by,
                    quality_status,
                    json.dumps(quality_report, ensure_ascii=False),
                    1 if publish_candidate else 0,
                    strategy,
                    window["end_date"] if window else "",
                ),
            )
            if publish_candidate:
                if window:
                    # 增量同步的 ODS 行来自多个窗口，不能按版本 prune。
                    prune_rejected_sync_versions(conn, live_row["source_key"])
                else:
                    prune_ods_table_versions(conn, live_row["source_key"], live_row["table_name"])
            else:
                prune_rejected_sync_versions(conn, live_row["source_key"])
            conn.commit()

        progress_status = quality_status if quality_status in {"warning", "empty", "rejected"} else "success"
        update_sync_progress_item(
            source,
            source_name=name,
            status=progress_status,
            fetched=resumed_row_count + row_count,
            total=reported_total or (resumed_row_count + row_count),
            page=last_page,
            row_count=resumed_row_count + row_count,
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
            "row_count": resumed_row_count + row_count,
            "fetched": resumed_row_count + row_count,
            "total": reported_total or (resumed_row_count + row_count),
            "message": quality_summary,
            "quality_status": quality_status,
            "quality_summary": quality_report.get("summary", ""),
            "_summary_key": quality_status,
        }
    except Exception as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        is_rejected = isinstance(exc, DatasourcePayloadError)
        outcome_status = "rejected" if is_rejected else "failed"
        outcome_row_count = row_count if is_rejected else 0
        failed_sync_version = sync_version or f"{started_at.replace('-', '').replace(':', '').replace(' ', '')}_{uuid.uuid4().hex[:8]}"
        quality_report = {
            "summary": detail,
            "published": False,
            "reason_code": exc.reason_code if is_rejected else "sync_failed",
            "page": exc.page if is_rejected else None,
            "row_count": outcome_row_count,
        }
        finished_at = now_text()
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        with get_connection() as conn:
            # For retriable failures we keep the staging table and checkpoint so the
            # next run can resume. For rejected payloads or non-retriable errors we
            # discard the candidate and reset the checkpoint.
            if staging_name:
                if is_rejected:
                    discard_ods_staging_rows(conn, staging_name)
                    reset_checkpoint(conn, source)
                else:
                    mark_checkpoint_failed(conn, source, detail)
                conn.commit()
            live_row = get_datasource_detail(conn, source, include_disabled=True)
            if live_row:
                conn.execute(
                    """
                    UPDATE sys_datasource
                    SET last_sync_at = ?, last_status = ?, last_message = ?,
                        last_quality_status = ?, last_quality_report = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        finished_at,
                        outcome_status,
                        detail,
                        outcome_status,
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
                        failed_sync_version,
                        outcome_status,
                        detail,
                        outcome_row_count,
                        started_at,
                        finished_at,
                        duration_ms,
                        triggered_by,
                        outcome_status,
                        json.dumps(quality_report, ensure_ascii=False),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO sys_sync_version (
                        source_key, source_name, table_name, sync_version, sync_batch_id,
                        status, message, row_count, started_at, finished_at, duration_ms,
                        triggered_by, quality_status, quality_report, is_current, strategy, watermark_value
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        live_row["source_key"],
                        live_row["source_name"],
                        live_row["table_name"],
                        failed_sync_version,
                        sync_batch_id if is_rejected else "",
                        outcome_status,
                        detail,
                        outcome_row_count,
                        started_at,
                        finished_at,
                        duration_ms,
                        triggered_by,
                        outcome_status,
                        json.dumps(quality_report, ensure_ascii=False),
                        strategy,
                        window["end_date"] if window else "",
                    ),
                )
                if is_rejected:
                    prune_rejected_sync_versions(conn, live_row["source_key"])
            conn.commit()
        update_sync_progress_item(
            source,
            source_name=name,
            status=outcome_status,
            fetched=outcome_row_count,
            total=reported_total if is_rejected else None,
            page=last_page or 1,
            row_count=outcome_row_count,
            message=detail,
            quality_status=outcome_status,
            quality_summary=detail,
            sync_version=failed_sync_version,
        )
        return {
            "source_key": source,
            "source_name": name,
            "status": outcome_status,
            "sync_version": failed_sync_version,
            "row_count": outcome_row_count,
            "fetched": outcome_row_count,
            "total": reported_total if is_rejected else None,
            "message": detail,
            "quality_status": outcome_status,
            "quality_summary": detail,
            "_summary_key": outcome_status,
        }


def run_sync(source_key: str | None = None, triggered_by: str = "system") -> dict[str, Any]:
    with get_connection() as conn:
        datasources = _load_target_datasources(conn, source_key)

    set_sync_running(True)
    clear_sync_cancel_request()
    start_sync_progress(datasources)

    finished_items: list[dict[str, Any]] = []
    summary = {"success": 0, "empty": 0, "warning": 0, "rejected": 0, "failed": 0, "total": len(datasources)}
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
