from __future__ import annotations

import json
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import now_text
from backend.app.schemas.datasource import (
    DatasourceCreateRequest,
    DatasourceDeleteRequest,
    DatasourceUpdateRequest,
    ParseDocRequest,
)
from backend.app.services.llm_service import get_llm_service_by_id, request_llm_doc_parse
from backend.app.services.auth_service import verify_password
from backend.app.services.datasource_service import (
    build_datasource_extra_config,
    get_datasource_detail,
    get_datasource_map,
    normalize_field_label_map,
    normalize_searchable_fields,
    parse_datasource_config,
    sanitize_request_config,
    serialize_datasource,
    sync_datasource_field_meta,
    table_exists,
)
from backend.app.integrations.datasource_runtime import list_remote_rows
from backend.app.services.sync_service import quote_identifier

router = APIRouter()


@router.post("/api/admin/datasource/test")
def admin_datasource_test(payload: DatasourceCreateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    try:
        rows, reported_total = list_remote_rows(
            {
                "source_key": payload.source_key.strip(),
                "http_method": payload.http_method,
                "api_url": payload.api_url.strip(),
                "verify_tls": payload.verify_tls,
                "request_config": sanitize_request_config(payload.source_key.strip(), payload.request_config, payload.token),
            },
            row_limit=5,
        )
        return {"status_code": 200, "row_count": int(reported_total or len(rows)), "message": "ok"}
    except HTTPException as exc:
        return {"status_code": exc.status_code, "row_count": 0, "message": exc.detail}
    except requests.RequestException as exc:
        return {"status_code": 500, "row_count": 0, "message": str(exc)}


@router.api_route("/api/admin/datasource/list", methods=["GET", "POST"])
def admin_datasource_list(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": [serialize_datasource(conn, row, admin) for row in conn.execute("SELECT * FROM sys_datasource ORDER BY id").fetchall()]}
    finally:
        conn.close()


@router.post("/api/admin/datasource/create")
def admin_datasource_create(payload: DatasourceCreateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        if conn.execute(
            "SELECT 1 FROM sys_datasource WHERE source_key = ? OR table_name = ? LIMIT 1",
            (payload.source_key.strip(), payload.table_name.strip()),
        ).fetchone():
            raise HTTPException(status_code=400, detail="Datasource key or table name already exists")
        default_cfg = get_datasource_map().get(payload.source_key.strip(), {})
        extra = build_datasource_extra_config(
            description=payload.description.strip(),
            chart_field=default_cfg.get("chart_field", ""),
            field_labels={
                **(default_cfg.get("field_labels", {}) if isinstance(default_cfg.get("field_labels"), dict) else {}),
                **normalize_field_label_map(payload.field_labels),
            },
            request_config=sanitize_request_config(payload.source_key.strip(), payload.request_config, payload.token),
            response_config=payload.response_config or {},
            searchable_fields=normalize_searchable_fields(payload.searchable_fields),
            quality_rules=payload.quality_rules,
            verify_tls=payload.verify_tls,
        )
        conn.execute(
            """
            INSERT INTO sys_datasource (source_key, source_name, table_name, http_method, api_url, extra_config, enabled, created_at, updated_at, platform_id)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                payload.source_key.strip(),
                payload.source_name.strip(),
                payload.table_name.strip(),
                payload.http_method,
                payload.api_url.strip(),
                json.dumps(extra, ensure_ascii=False),
                now_text(),
                now_text(),
                payload.platform_id,
            ),
        )
        created = get_datasource_detail(conn, payload.source_key.strip(), include_disabled=True)
        if created:
            sync_datasource_field_meta(conn, created)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "create_datasource", "sys_datasource", payload.source_key.strip())
        return {"message": "Datasource created"}
    finally:
        conn.close()


@router.put("/api/admin/datasource/{datasource_id}")
def admin_datasource_update(datasource_id: int, payload: DatasourceUpdateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sys_datasource WHERE id = ? LIMIT 1", (datasource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Datasource not found")
        config = parse_datasource_config(row)
        if payload.description is not None:
            config["description"] = payload.description.strip()
        if payload.searchable_fields is not None:
            config["searchable_fields"] = normalize_searchable_fields(payload.searchable_fields)
        if payload.quality_rules is not None:
            config["quality_rules"] = payload.quality_rules
        if payload.verify_tls is not None:
            config["verify_tls"] = payload.verify_tls
        if payload.field_labels is not None:
            defaults = get_datasource_map().get(str(row["source_key"] or "").strip(), {})
            config["field_labels"] = {
                **(defaults.get("field_labels", {}) if isinstance(defaults.get("field_labels"), dict) else {}),
                **normalize_field_label_map(payload.field_labels),
            }
        if payload.request_config is not None:
            config["request"] = sanitize_request_config(row["source_key"], payload.request_config, payload.token or "")
        if payload.response_config is not None:
            config["response"] = payload.response_config
        conn.execute(
            """
            UPDATE sys_datasource
            SET source_name = ?, http_method = ?, api_url = ?, platform_id = ?, extra_config = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.source_name.strip() if payload.source_name is not None else row["source_name"],
                payload.http_method or row["http_method"],
                payload.api_url.strip() if payload.api_url is not None else (row["api_url"] or ""),
                payload.platform_id,
                json.dumps(config, ensure_ascii=False),
                now_text(),
                datasource_id,
            ),
        )
        updated = conn.execute("SELECT * FROM sys_datasource WHERE id = ? LIMIT 1", (datasource_id,)).fetchone()
        if updated:
            sync_datasource_field_meta(conn, updated)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "update_datasource", "sys_datasource", f"id={datasource_id}")
        return {"message": "Saved"}
    finally:
        conn.close()


@router.post("/api/admin/datasource/{datasource_id}/delete")
def admin_datasource_delete(datasource_id: int, payload: DatasourceDeleteRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sys_datasource WHERE id = ? LIMIT 1", (datasource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Datasource not found")
        if not verify_password(payload.admin_password, admin["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid admin password")
        source_key = str(row["source_key"] or "")
        table_name = str(row["table_name"] or "")
        conn.execute("DELETE FROM sys_user_permission WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_department_permission WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_user_field_permission WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_department_field_permission WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_field_meta WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_sync_log WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_sync_version WHERE source_key = ?", (source_key,))
        conn.execute("DELETE FROM sys_datasource WHERE id = ?", (datasource_id,))
        if table_name and table_exists(conn, table_name):
            conn.execute(f"DROP TABLE {quote_identifier(table_name)}")
        conn.commit()
        record_audit_log(
            admin["username"],
            admin["role"],
            "delete_datasource",
            "sys_datasource",
            f"id={datasource_id};source_key={source_key};table={table_name}",
        )
        return {"message": "Datasource deleted", "id": datasource_id, "source_key": source_key, "table_name": table_name}
    finally:
        conn.close()


@router.post("/api/admin/datasource/parse-doc")
def admin_datasource_parse_doc(payload: ParseDocRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        service = get_llm_service_by_id(conn, payload.service_id, include_secret=True)
        if not service:
            raise HTTPException(status_code=404, detail="LLM service not found")
        if not service.get("enabled"):
            raise HTTPException(status_code=400, detail="LLM service is disabled")
        result = request_llm_doc_parse(service, payload.document_text, payload.filename)
        record_audit_log(admin["username"], admin["role"], "parse_doc", "sys_datasource", f"service={service.get('name')}")
        return result
    finally:
        conn.close()
