from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.app.api.deps import get_current_user
from backend.app.db.connection import get_connection
from backend.app.services.datasource_service import list_catalog_items, list_platform_catalog

router = APIRouter()


@router.get("/api/catalog")
def catalog(keyword: str = "", user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": list_catalog_items(conn, user, keyword=keyword)}
    finally:
        conn.close()


@router.get("/api/datasources")
def datasources(user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": list_catalog_items(conn, user, keyword="")}
    finally:
        conn.close()


@router.get("/api/platforms")
def platforms(user=Depends(get_current_user)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {"items": list_platform_catalog(conn, user)}
    finally:
        conn.close()
