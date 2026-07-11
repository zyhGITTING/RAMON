from __future__ import annotations

from fastapi import HTTPException


def list_platforms(conn) -> list[dict]:
    items = []
    for row in conn.execute("SELECT * FROM sys_platform ORDER BY sort_order, id").fetchall():
        ds_count = int(conn.execute("SELECT COUNT(*) AS c FROM sys_datasource WHERE platform_id = ?", (row["id"],)).fetchone()["c"])
        active_count = int(
            conn.execute("SELECT COUNT(*) AS c FROM sys_datasource WHERE platform_id = ? AND enabled = 1", (row["id"],)).fetchone()["c"]
        )
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"] or "",
                "datasource_count": ds_count,
                "active_datasource_count": active_count,
            }
        )
    return items


def create_platform(conn, name: str, description: str = "") -> None:
    sort_order = int(conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM sys_platform").fetchone()["n"])
    conn.execute(
        "INSERT INTO sys_platform (name, description, created_at, sort_order) VALUES (?, ?, ?, ?)",
        (name.strip(), description.strip(), conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()["now"], sort_order),
    )


def update_platform(conn, platform_id: int, name: str, description: str = "") -> None:
    row = conn.execute("SELECT 1 FROM sys_platform WHERE id = ? LIMIT 1", (platform_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Platform not found")
    conn.execute("UPDATE sys_platform SET name = ?, description = ? WHERE id = ?", (name.strip(), description.strip(), platform_id))


def delete_platform(conn, platform_id: int) -> None:
    active_count = int(
        conn.execute("SELECT COUNT(*) AS c FROM sys_datasource WHERE platform_id = ? AND enabled = 1", (platform_id,)).fetchone()["c"]
    )
    if active_count:
        raise HTTPException(status_code=400, detail="Active datasources still exist under this platform")
    conn.execute("UPDATE sys_datasource SET platform_id = NULL WHERE platform_id = ?", (platform_id,))
    conn.execute("DELETE FROM sys_platform WHERE id = ?", (platform_id,))


def reorder_platforms(conn, ids: list[int]) -> None:
    for idx, platform_id in enumerate(ids, start=1):
        conn.execute("UPDATE sys_platform SET sort_order = ? WHERE id = ?", (idx, platform_id))
