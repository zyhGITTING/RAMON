from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta

from backend.app.db.connection import get_connection
from backend.app.db.repositories.config import get_config
from backend.app.services.sync_service import (
    DEFAULT_SYNC_INTERVAL_SECONDS,
    get_effective_interval_seconds,
    launch_sync_job,
)
from backend.app.services.sync_progress_service import is_sync_running


_BACKEND_SCHEDULER_STARTED = False
_BACKEND_SCHEDULER_LOCK = threading.Lock()


def _pick_next_due_source() -> str | None:
    """返回最 overdue 的启用数据源 source_key；没有到期则返回 None。"""
    conn = get_connection()
    try:
        now_dt = datetime.now()
        rows = conn.execute(
            "SELECT * FROM sys_datasource WHERE enabled = 1 ORDER BY COALESCE(platform_id, 9999), id"
        ).fetchall()
        candidates = []
        for ds in rows:
            interval = get_effective_interval_seconds(conn, ds)
            last_sync_at = ds.get("last_sync_at") or ""
            if not last_sync_at:
                return ds["source_key"]
            try:
                if isinstance(last_sync_at, datetime):
                    last_dt = last_sync_at
                    if last_dt.tzinfo is not None:
                        last_dt = last_dt.replace(tzinfo=None)
                else:
                    last_dt = datetime.strptime(str(last_sync_at), "%Y-%m-%d %H:%M:%S")
                next_due = last_dt + timedelta(seconds=interval)
                if next_due <= now_dt:
                    candidates.append((ds["source_key"], next_due))
            except ValueError:
                return ds["source_key"]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    finally:
        conn.close()


def _check_and_run_auto_sync() -> None:
    """检查并触发定时同步。异常内部吞掉，避免调度线程退出。"""
    try:
        conn = get_connection()
        try:
            auto_enabled = get_config(conn, "auto_sync_enabled", "0") == "1"
        finally:
            conn.close()

        if not auto_enabled or is_sync_running():
            return

        due_source_key = _pick_next_due_source()
        if due_source_key:
            launch_sync_job(due_source_key, "scheduler")
    except Exception:
        # 调度线程不能因为一次异常就退出
        pass


def scheduler_loop() -> None:
    """后台调度循环，每 5 秒检查一次定时同步。"""
    while True:
        _check_and_run_auto_sync()
        time.sleep(5)


def ensure_scheduler_started() -> None:
    """启动后台调度线程（幂等）。"""
    global _BACKEND_SCHEDULER_STARTED
    with _BACKEND_SCHEDULER_LOCK:
        if _BACKEND_SCHEDULER_STARTED:
            return
        _BACKEND_SCHEDULER_STARTED = True
    thread = threading.Thread(target=scheduler_loop, name="datamid-backend-scheduler", daemon=True)
    thread.start()
