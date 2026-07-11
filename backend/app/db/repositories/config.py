from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def get_config(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM sys_config WHERE key = ? LIMIT 1", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn, key: str, value: str) -> None:
    if conn.execute("SELECT 1 FROM sys_config WHERE key = ? LIMIT 1", (key,)).fetchone():
        conn.execute("UPDATE sys_config SET value = ?, updated_at = ? WHERE key = ?", (value, now_text(), key))
    else:
        conn.execute("INSERT INTO sys_config (key, value, updated_at) VALUES (?, ?, ?)", (key, value, now_text()))
