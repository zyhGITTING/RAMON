from __future__ import annotations

from fastapi import Depends

from backend.app.services.auth_service import (
    get_current_user as auth_get_current_user,
    require_admin as auth_require_admin,
    security,
)


def get_current_user(credentials=Depends(security)):
    return auth_get_current_user(credentials)


def require_admin(user=Depends(get_current_user)):
    return auth_require_admin(user)
