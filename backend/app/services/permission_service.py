from __future__ import annotations

def resolve_permission_origin(conn, user, source_key: str) -> str:
    if user["role"] == "admin":
        return "admin"
    user_hit = conn.execute(
        "SELECT 1 FROM sys_user_permission WHERE user_id = ? AND source_key = ? LIMIT 1",
        (user["id"], source_key),
    ).fetchone()
    dept = (user["department"] or "").strip()
    dept_hit = (
        conn.execute(
            "SELECT 1 FROM sys_department_permission WHERE department = ? AND source_key = ? LIMIT 1",
            (dept, source_key),
        ).fetchone()
        if dept
        else None
    )
    if user_hit and dept_hit:
        return "both"
    if user_hit:
        return "user"
    if dept_hit:
        return "department"
    return "none"

def get_restricted_field_map(conn, source_key: str) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        "SELECT field_name, restricted_access, mask_rule FROM sys_field_meta "
        "WHERE source_key = ? AND is_active = 1 AND is_restricted = 1",
        (source_key,),
    ).fetchall()
    return {
        str(row["field_name"]): {
            "access": (row["restricted_access"] or "hide"),
            "mask_rule": row["mask_rule"] or "",
        }
        for row in rows
    }


def get_user_field_grants(conn, user, source_key: str) -> set[str]:
    grants = {
        str(row["field_name"])
        for row in conn.execute(
            "SELECT field_name FROM sys_user_field_permission WHERE user_id = ? AND source_key = ?",
            (user["id"], source_key),
        ).fetchall()
    }
    dept = (user["department"] or "").strip()
    if dept:
        grants.update(
            str(row["field_name"])
            for row in conn.execute(
                "SELECT field_name FROM sys_department_field_permission WHERE department = ? AND source_key = ?",
                (dept, source_key),
            ).fetchall()
        )
    return grants


def resolve_field_access(conn, user, source_key: str, columns: list[str]) -> dict[str, str]:
    if user is not None and user["role"] == "admin":
        return {column: "plain" for column in columns}
    restricted = get_restricted_field_map(conn, source_key)
    grants = get_user_field_grants(conn, user, source_key) if user is not None else set()
    access: dict[str, str] = {}
    for column in columns:
        if column not in restricted or column in grants:
            access[column] = "plain"
        else:
            access[column] = "mask" if restricted[column]["access"] == "mask" else "hide"
    return access


def get_effective_source_keys(conn, user) -> set[str]:
    if user["role"] == "admin":
        return {row["source_key"] for row in conn.execute("SELECT source_key FROM sys_datasource WHERE enabled = 1").fetchall()}
    keys = {row["source_key"] for row in conn.execute("SELECT source_key FROM sys_user_permission WHERE user_id = ?", (user["id"],)).fetchall()}
    dept = (user["department"] or "").strip()
    if dept:
        keys.update(row["source_key"] for row in conn.execute("SELECT source_key FROM sys_department_permission WHERE department = ?", (dept,)).fetchall())
    return keys


def has_source_permission(conn, user, source_key: str) -> bool:
    return source_key in get_effective_source_keys(conn, user)
