from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.app.api.deps import require_admin
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import now_text
from backend.app.schemas.llm import LlmServiceCreateRequest, LlmServiceUpdateRequest
from backend.app.services.llm_service import (
    get_llm_service_by_id,
    list_llm_services,
    load_llm_services,
    normalize_llm_service,
    save_llm_services,
)

router = APIRouter()


@router.api_route("/api/admin/llm-service/list", methods=["GET", "POST"])
def admin_llm_service_list(admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": list_llm_services(conn)}
    finally:
        conn.close()


@router.post("/api/admin/llm-service/create")
def admin_llm_service_create(payload: LlmServiceCreateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        items = load_llm_services(conn)
        next_id = max((int(item["id"]) for item in items), default=0) + 1
        item = normalize_llm_service(
            {
                "id": next_id,
                "name": payload.name,
                "base_url": payload.base_url,
                "api_key": payload.api_key,
                "model": payload.model,
                "enabled": payload.enabled,
                "is_default": payload.is_default,
                "verify_tls": payload.verify_tls,
                "created_at": now_text(),
                "updated_at": now_text(),
            }
        )
        if not item or not item["name"] or not item["base_url"] or not item["api_key"] or not item["model"]:
            raise HTTPException(status_code=400, detail="Invalid LLM service payload")
        if item["is_default"] or not items:
            for existing in items:
                existing["is_default"] = False
            item["is_default"] = True
        items.append(item)
        save_llm_services(conn, items)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "create_llm_service", "sys_config", f"id={next_id};name={item['name']}")
        return {"message": "LLM service created", "id": next_id}
    finally:
        conn.close()


@router.put("/api/admin/llm-service/{service_id}")
def admin_llm_service_update(service_id: int, payload: LlmServiceUpdateRequest, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        items = load_llm_services(conn)
        current = next((item for item in items if int(item["id"]) == int(service_id)), None)
        if not current:
            raise HTTPException(status_code=404, detail="LLM service not found")
        updated = normalize_llm_service(
            {
                **current,
                "name": payload.name if payload.name is not None else current["name"],
                "base_url": payload.base_url if payload.base_url is not None else current["base_url"],
                "api_key": payload.api_key.strip() if payload.api_key is not None and payload.api_key.strip() else current["api_key"],
                "model": payload.model if payload.model is not None else current["model"],
                "enabled": payload.enabled if payload.enabled is not None else current["enabled"],
                "is_default": payload.is_default if payload.is_default is not None else current["is_default"],
                "verify_tls": payload.verify_tls if payload.verify_tls is not None else current["verify_tls"],
                "created_at": current["created_at"],
                "updated_at": now_text(),
            }
        )
        if not updated or not updated["name"] or not updated["base_url"] or not updated["api_key"] or not updated["model"]:
            raise HTTPException(status_code=400, detail="Invalid LLM service payload")
        if updated["is_default"]:
            for item in items:
                item["is_default"] = False
        for idx, item in enumerate(items):
            if int(item["id"]) == int(service_id):
                items[idx] = updated
                break
        if items and not any(item.get("is_default") for item in items):
            items[0]["is_default"] = True
        save_llm_services(conn, items)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "update_llm_service", "sys_config", f"id={service_id};name={updated['name']}")
        return {"message": "LLM service updated"}
    finally:
        conn.close()


@router.delete("/api/admin/llm-service/{service_id}")
def admin_llm_service_delete(service_id: int, admin=Depends(require_admin)) -> dict[str, Any]:
    conn = get_connection()
    try:
        current = get_llm_service_by_id(conn, service_id, include_secret=True)
        if not current:
            raise HTTPException(status_code=404, detail="LLM service not found")
        items = [item for item in load_llm_services(conn) if int(item["id"]) != int(service_id)]
        if items and not any(item.get("is_default") for item in items):
            items[0]["is_default"] = True
        save_llm_services(conn, items)
        conn.commit()
        record_audit_log(admin["username"], admin["role"], "delete_llm_service", "sys_config", f"id={service_id};name={current['name']}")
        return {"message": "LLM service deleted"}
    finally:
        conn.close()
