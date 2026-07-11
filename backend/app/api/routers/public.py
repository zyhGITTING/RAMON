from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from backend.app.core.config import FRONTEND_HTML_PATH, LEIMO_PATH, LOGO_PATH
from backend.app.db.repositories.config import now_text
from backend.app.services.auth_service import ALLOW_SELF_REGISTER, DEV_SSO_TOKEN, YANHUANG_SSO_SECRET

router = APIRouter()


@router.get("/")
def home() -> FileResponse:
    return FileResponse(FRONTEND_HTML_PATH)


@router.get("/dashboard.html")
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_HTML_PATH)


@router.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(LOGO_PATH)


@router.get("/leimo.png")
def leimo() -> FileResponse:
    return FileResponse(LEIMO_PATH)


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": now_text()}


@router.get("/api/public/config")
def public_config() -> dict[str, Any]:
    return {
        "self_register_enabled": ALLOW_SELF_REGISTER,
        "dev_sso_enabled": bool(DEV_SSO_TOKEN),
        "yanhuang_sso_enabled": bool(YANHUANG_SSO_SECRET),
    }
