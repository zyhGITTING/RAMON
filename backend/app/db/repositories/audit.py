from __future__ import annotations

from backend.app.db.connection import get_connection
from backend.app.db.repositories.config import now_text


def record_audit_log(
    username: str,
    role: str,
    action: str,
    target: str,
    detail: str = "",
    ip: str = "",
    *,
    user_id: int | None = None,
    employee_no: str = "",
    department: str = "",
    token_id: int | None = None,
    jti: str = "",
    source_name: str = "",
    keyword: str = "",
    as_of: str = "",
    page: int | None = None,
    page_size: int | None = None,
    row_count: int | None = None,
    total_count: int | None = None,
    search_fields: str = "",
    accessed_fields: str = "",
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO sys_audit_log (
                username, role, action, target, detail, ip, created_at,
                user_id, employee_no, department, token_id, jti, source_name,
                keyword, as_of, page, page_size, row_count, total_count,
                search_fields, accessed_fields
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                role,
                action,
                target,
                detail,
                ip,
                now_text(),
                user_id,
                employee_no,
                department,
                token_id,
                jti,
                source_name,
                keyword,
                as_of,
                page,
                page_size,
                row_count,
                total_count,
                search_fields,
                accessed_fields,
            ),
        )
        conn.commit()
    finally:
        conn.close()
