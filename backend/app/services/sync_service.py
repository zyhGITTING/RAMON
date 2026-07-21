from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from backend.app.db.repositories.config import get_config, now_text, set_config
from backend.app.db.repositories.sync_checkpoint import get_checkpoint, reset_checkpoint
from backend.app.services.datasource_service import (
    ensure_ods_table,
    get_datasource_detail,
    table_exists,
)
from backend.app.services.sync_progress_service import (
    is_sync_running,
    request_sync_cancel,
    snapshot_sync_progress,
)
from backend.sync_data import run_sync as backend_run_sync

DEFAULT_SYNC_INTERVAL_SECONDS = 3600

__all__ = [
    "run_sync",
    "launch_sync_job",
    "quote_identifier",
    "get_sync_version_row",
    "set_current_sync_version",
    "get_effective_interval_seconds",
    "schedule_next_auto_sync",
    "get_sync_status_payload",
    "is_sync_running",
    "request_sync_cancel",
    "snapshot_sync_progress",
    "reset_datasource_checkpoint",
]


def run_sync(source_key: str | None = None, triggered_by: str = "system") -> dict[str, Any]:
    return backend_run_sync(source_key=source_key, triggered_by=triggered_by)


def launch_sync_job(source_key: str | None, triggered_by: str) -> dict[str, Any]:
    if is_sync_running():
        raise HTTPException(status_code=400, detail="Sync task is already running")

    def runner() -> None:
        run_sync(source_key=source_key, triggered_by=triggered_by)

    thread = threading.Thread(target=runner, name=f"datamid-sync-{source_key or 'all'}", daemon=True)
    thread.start()
    return {"message": "Sync task started", "source_key": source_key or "", "triggered_by": triggered_by}


def get_effective_interval_seconds(conn, datasource=None) -> int:
    if datasource and datasource.get("sync_interval_seconds"):
        return int(datasource["sync_interval_seconds"])
    return int(get_config(conn, "sync_interval_seconds", str(DEFAULT_SYNC_INTERVAL_SECONDS)) or DEFAULT_SYNC_INTERVAL_SECONDS)


def schedule_next_auto_sync(conn, seconds: int) -> None:
    set_config(conn, "next_auto_sync_at", (datetime.now() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S"))


def get_sync_status_payload(conn) -> dict[str, Any]:
    auto_enabled = get_config(conn, "auto_sync_enabled", "0") == "1"
    interval_seconds = int(get_config(conn, "sync_interval_seconds", str(DEFAULT_SYNC_INTERVAL_SECONDS)) or DEFAULT_SYNC_INTERVAL_SECONDS)
    cooldowns: dict[str, Any] = {}
    now_dt = datetime.now()
    for ds in conn.execute("SELECT * FROM sys_datasource ORDER BY COALESCE(platform_id, 9999), id").fetchall():
        next_sync_in = None
        if ds["last_sync_at"]:
            try:
                last_sync_at = ds["last_sync_at"]
                if isinstance(last_sync_at, datetime):
                    last_dt = last_sync_at
                    if last_dt.tzinfo is not None:
                        last_dt = last_dt.replace(tzinfo=None)
                else:
                    last_dt = datetime.strptime(last_sync_at, "%Y-%m-%d %H:%M:%S")
                next_dt = last_dt + timedelta(seconds=get_effective_interval_seconds(conn, ds))
                next_sync_in = max(0, int((next_dt - now_dt).total_seconds()))
            except ValueError:
                next_sync_in = None
        checkpoint = get_checkpoint(conn, ds["source_key"]) or {}
        cooldowns[ds["source_key"]] = {
            "source_key": ds["source_key"],
            "source_name": ds["source_name"],
            "last_status": ds["last_status"] or "",
            "last_sync_at": ds["last_sync_at"],
            "remaining": next_sync_in or 0,
            "next_sync_in": next_sync_in,
            "sync_interval_seconds": ds.get("sync_interval_seconds"),
            "checkpoint": {
                "status": checkpoint.get("status", "completed"),
                "strategy": checkpoint.get("strategy", "full"),
                "last_fetched_page": int(checkpoint.get("last_fetched_page") or 0),
                "last_fetched_row_count": int(checkpoint.get("last_fetched_row_count") or 0),
                "failed_attempts": int(checkpoint.get("failed_attempts") or 0),
                "last_error": checkpoint.get("last_error", "") or "",
                "updated_at": checkpoint.get("updated_at"),
            } if checkpoint else None,
        }
    seconds_until_next = None
    if auto_enabled:
        values = [v.get("remaining", 0) for v in cooldowns.values() if v.get("remaining") is not None]
        if values:
            seconds_until_next = max(0, min(values))
        else:
            seconds_until_next = 0
    return {
        "auto_enabled": auto_enabled,
        "interval_seconds": interval_seconds,
        "is_syncing": is_sync_running(),
        "seconds_until_next": seconds_until_next,
        "cooldowns": cooldowns,
        "progress": snapshot_sync_progress(),
    }


def reset_datasource_checkpoint(conn, source_key: str, *, force_full_sync: bool = True) -> dict[str, Any]:
    """Truncate staging and reset checkpoint so the next sync starts fresh."""
    ds = get_datasource_detail(conn, source_key, include_disabled=True)
    if not ds:
        raise HTTPException(status_code=404, detail="Datasource not found")
    staging_name = f"stg_{ds['table_name']}"
    if table_exists(conn, staging_name):
        conn.execute(f"TRUNCATE TABLE {quote_identifier(staging_name)}")
    reset_checkpoint(conn, source_key, strategy="full")
    return {
        "source_key": source_key,
        "message": "Checkpoint reset; next sync will start from page 1",
        "staging_truncated": table_exists(conn, staging_name),
    }


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def get_sync_version_row(conn, source_key: str, sync_version: str):
    return conn.execute(
        """
        SELECT *
        FROM sys_sync_version
        WHERE source_key = ? AND sync_version = ?
        LIMIT 1
        """,
        (source_key, sync_version),
    ).fetchone()


def set_current_sync_version(conn, datasource, sync_version: str):
    version = get_sync_version_row(conn, datasource["source_key"], sync_version)
    if not version:
        raise HTTPException(status_code=404, detail="Snapshot version not found")
    if version["status"] not in {"success", "warning", "empty"}:
        raise HTTPException(status_code=400, detail="Only successful snapshot versions can be rolled back")
    ensure_ods_table(conn, datasource["table_name"], [])
    conn.execute("UPDATE sys_sync_version SET is_current = 0 WHERE source_key = ?", (datasource["source_key"],))
    conn.execute("UPDATE sys_sync_version SET is_current = 1 WHERE source_key = ? AND sync_version = ?", (datasource["source_key"], sync_version))
    conn.execute(f"UPDATE {quote_identifier(datasource['table_name'])} SET is_current = 0 WHERE is_current = 1")
    conn.execute(
        f"UPDATE {quote_identifier(datasource['table_name'])} SET is_current = 1 WHERE sync_version = ?",
        (sync_version,),
    )
    conn.execute(
        """
        UPDATE sys_datasource
        SET last_sync_at = ?, last_status = ?, last_message = ?, last_quality_status = ?, last_quality_report = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            version["finished_at"],
            version["status"],
            version["message"],
            version["quality_status"],
            version["quality_report"],
            now_text(),
            datasource["id"],
        ),
    )
    return version
