from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


_STALE_HOURS = 24


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def get_checkpoint(conn, source_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM sys_sync_checkpoint WHERE source_key = ? LIMIT 1",
        (source_key,),
    ).fetchone()
    return _row_to_dict(row)


def create_checkpoint(
    conn,
    source_key: str,
    sync_batch_id: str,
    sync_version: str,
    strategy: str = "full",
    watermark_value: str = "",
    cursor_value: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO sys_sync_checkpoint (
            source_key, sync_batch_id, sync_version, strategy, status,
            watermark_value, cursor_value, last_fetched_page, last_fetched_row_count,
            failed_attempts, last_error, start_date, end_date, started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_key) DO UPDATE SET
            sync_batch_id = EXCLUDED.sync_batch_id,
            sync_version = EXCLUDED.sync_version,
            strategy = EXCLUDED.strategy,
            status = EXCLUDED.status,
            watermark_value = EXCLUDED.watermark_value,
            cursor_value = EXCLUDED.cursor_value,
            last_fetched_page = EXCLUDED.last_fetched_page,
            last_fetched_row_count = EXCLUDED.last_fetched_row_count,
            failed_attempts = EXCLUDED.failed_attempts,
            last_error = EXCLUDED.last_error,
            start_date = EXCLUDED.start_date,
            end_date = EXCLUDED.end_date,
            started_at = EXCLUDED.started_at,
            updated_at = EXCLUDED.updated_at
        """,
        (
            source_key,
            sync_batch_id,
            sync_version,
            strategy,
            "running",
            watermark_value,
            cursor_value,
            0,
            0,
            0,
            "",
            start_date,
            end_date,
            now,
            now,
        ),
    )
    return get_checkpoint(conn, source_key) or {}


def update_checkpoint_progress(
    conn,
    source_key: str,
    page: int,
    row_count: int,
    watermark_value: str | None = None,
    cursor_value: str | None = None,
    error: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    sets = [
        "last_fetched_page = ?",
        "last_fetched_row_count = ?",
        "updated_at = ?",
    ]
    params: list[Any] = [page, row_count, _utc_now()]
    if watermark_value is not None:
        sets.append("watermark_value = ?")
        params.append(watermark_value)
    if cursor_value is not None:
        sets.append("cursor_value = ?")
        params.append(cursor_value)
    if error is not None:
        sets.append("last_error = ?")
        params.append(error)
    if start_date is not None:
        sets.append("start_date = ?")
        params.append(start_date)
    if end_date is not None:
        sets.append("end_date = ?")
        params.append(end_date)
    params.append(source_key)
    conn.execute(
        f"UPDATE sys_sync_checkpoint SET {', '.join(sets)} WHERE source_key = ?",
        params,
    )
    return get_checkpoint(conn, source_key) or {}


def mark_checkpoint_completed(conn, source_key: str) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE sys_sync_checkpoint
        SET status = 'completed', last_error = '', failed_attempts = 0, updated_at = ?
        WHERE source_key = ?
        """,
        (_utc_now(), source_key),
    )
    return get_checkpoint(conn, source_key) or {}


def mark_checkpoint_failed(
    conn,
    source_key: str,
    error: str,
    failed_attempts: int | None = None,
) -> dict[str, Any]:
    if failed_attempts is None:
        existing = get_checkpoint(conn, source_key)
        failed_attempts = int(existing.get("failed_attempts") or 0) + 1 if existing else 1
    conn.execute(
        """
        UPDATE sys_sync_checkpoint
        SET status = 'failed', last_error = ?, failed_attempts = ?, updated_at = ?
        WHERE source_key = ?
        """,
        (error, failed_attempts, _utc_now(), source_key),
    )
    return get_checkpoint(conn, source_key) or {}


def reset_checkpoint(
    conn,
    source_key: str,
    strategy: str = "full",
) -> dict[str, Any]:
    """Reset checkpoint to a clean completed state and clear any error counters."""
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO sys_sync_checkpoint (
            source_key, sync_batch_id, sync_version, strategy, status,
            watermark_value, cursor_value, last_fetched_page, last_fetched_row_count,
            failed_attempts, last_error, start_date, end_date, started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_key) DO UPDATE SET
            sync_batch_id = EXCLUDED.sync_batch_id,
            sync_version = EXCLUDED.sync_version,
            strategy = EXCLUDED.strategy,
            status = EXCLUDED.status,
            watermark_value = EXCLUDED.watermark_value,
            cursor_value = EXCLUDED.cursor_value,
            last_fetched_page = EXCLUDED.last_fetched_page,
            last_fetched_row_count = EXCLUDED.last_fetched_row_count,
            failed_attempts = EXCLUDED.failed_attempts,
            last_error = EXCLUDED.last_error,
            start_date = EXCLUDED.start_date,
            end_date = EXCLUDED.end_date,
            started_at = EXCLUDED.started_at,
            updated_at = EXCLUDED.updated_at
        """,
        (
            source_key,
            "",
            "",
            strategy,
            "completed",
            "",
            "",
            0,
            0,
            0,
            "",
            "",
            "",
            now,
            now,
        ),
    )
    return get_checkpoint(conn, source_key) or {}


def is_checkpoint_stale(checkpoint: dict[str, Any] | None, hours: int = _STALE_HOURS) -> bool:
    if not checkpoint:
        return True
    updated_at = checkpoint.get("updated_at")
    if not updated_at:
        return True
    try:
        if isinstance(updated_at, datetime):
            updated_dt = updated_at
        else:
            updated_dt = datetime.fromisoformat(str(updated_at))
        if updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
        return _utc_now() - updated_dt > timedelta(hours=hours)
    except (TypeError, ValueError):
        return True
