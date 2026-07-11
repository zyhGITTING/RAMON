from __future__ import annotations

import hmac
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.app.api.deps import get_current_user
from backend.app.db.connection import get_connection
from backend.app.db.repositories.audit import record_audit_log
from backend.app.db.repositories.config import now_text
from backend.app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest
from backend.app.services.auth_service import (
    ALLOW_SELF_REGISTER,
    DEV_SSO_TOKEN,
    DEV_SSO_USER,
    URL_SSO_AUTO_REGISTER,
    URL_SSO_ENABLED,
    URL_SSO_TRUSTED_ORIGINS,
    YANHUANG_SSO_SECRET,
    authenticate_user,
    clear_login_failures,
    create_access_token,
    enforce_login_rate_limit,
    generate_default_temp_password,
    get_user_by_username,
    hash_password,
    record_login_failure,
    resolve_client_ip,
    serialize_user,
    validate_password_strength,
    verify_password,
    verify_yanhuang_sso_signature,
)

router = APIRouter()


@router.post("/api/auth/login")
def auth_login(request: Request, payload: LoginRequest) -> dict[str, Any]:
    username = payload.username.strip()
    client_ip = resolve_client_ip(request)
    enforce_login_rate_limit(username, client_ip)
    user = authenticate_user(username, payload.password)
    if not user:
        record_login_failure(username, client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    clear_login_failures(username, client_ip)
    return {"token": create_access_token(user), "user": serialize_user(user)}


@router.post("/api/auth/register")
def auth_register(payload: RegisterRequest) -> dict[str, Any]:
    if not ALLOW_SELF_REGISTER:
        raise HTTPException(status_code=403, detail="Self registration is disabled")
    username = payload.username.strip()
    employee_no = payload.employee_no.strip()
    if not re.match(r"^120\d{7}$", username):
        raise HTTPException(status_code=400, detail="用户名必须是 120 开头的 10 位工号")
    if employee_no != username:
        raise HTTPException(status_code=400, detail="工号必须与用户名一致")
    error = validate_password_strength(payload.password, username=username, employee_no=employee_no)
    if error:
        raise HTTPException(status_code=400, detail=error)
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM sys_user WHERE username = ? OR employee_no = ? LIMIT 1", (username, employee_no)).fetchone():
            raise HTTPException(status_code=400, detail="Username or employee number already exists")
        conn.execute(
            "INSERT INTO sys_user (employee_no, username, full_name, password_hash, role, department, created_at, updated_at) VALUES (?, ?, ?, ?, 'user', '', ?, ?)",
            (employee_no, username, payload.full_name.strip(), hash_password(payload.password), now_text(), now_text()),
        )
        conn.commit()
        user = get_user_by_username(conn, username)
    finally:
        conn.close()
    return {"token": create_access_token(user), "user": serialize_user(user)}


@router.post("/api/auth/me")
def auth_me(user=Depends(get_current_user)) -> dict[str, Any]:
    return {"user": serialize_user(user)}


@router.post("/api/auth/change-password")
def auth_change_password(request: Request, payload: ChangePasswordRequest, user=Depends(get_current_user)) -> dict[str, Any]:
    # 强制改密码（must_change_password=1）时不校验原密码：初始密码是系统按"工号+工号后三位"这个
    # 公开规则生成的，本来就不算什么秘密，要求用户再输一遍没有实际安全意义。
    # 但用户之后自己主动改密码（这时 must_change_password 已经是 0）时，原密码是本人真正设置过的，
    # 这里必须校验，防止会话被盗用后被人直接篡改密码顶号。
    if not user["must_change_password"] and not verify_password(payload.old_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="原密码不正确")
    error = validate_password_strength(payload.new_password, username=user["username"], employee_no=user["employee_no"])
    if error:
        raise HTTPException(status_code=400, detail=error)
    if verify_password(payload.new_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sys_user SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
            (hash_password(payload.new_password), now_text(), user["id"]),
        )
        conn.commit()
        updated = get_user_by_username(conn, user["username"])
    finally:
        conn.close()
    record_audit_log(user["username"], user["role"], "change_password", "sys_user", f"id={user['id']}", resolve_client_ip(request))
    return {"message": "密码已更新", "user": serialize_user(updated)}


if DEV_SSO_TOKEN:
    @router.get("/api/auth/sso")
    def auth_sso(token: str = "") -> dict[str, Any]:
        if not hmac.compare_digest(token.strip(), DEV_SSO_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid SSO token")
        conn = get_connection()
        try:
            user = get_user_by_username(conn, DEV_SSO_USER)
        finally:
            conn.close()
        if not user:
            raise HTTPException(status_code=401, detail="SSO user not found")
        return {"token": create_access_token(user), "user": serialize_user(user)}


# 炎黄平台跳转过来只负责"自动开号"，不负责"免密登录"：
# 首次跳转过来自动注册（工号为账号，密码=工号+工号后三位，强制标记为必须改密码），
# 之后每次都还是要在登录页手动输密码——这是刻意的设计，不是漏做了自动登录：
# 目的是保留"每次进系统都要验证一次密码"这道门槛，同时借助炎黄给的身份信息把开号这一步自动化掉，
# 审计日志里天然就能按工号/姓名区分是谁做的操作，不需要再靠自动登录来"省一步"。
# 只要没配 DATAMID_YANHUANG_SSO_SECRET，这条路由完全不会注册，默认关闭、零风险。
if YANHUANG_SSO_SECRET:
    @router.get("/api/auth/sso/yanhuang")
    def auth_sso_yanhuang(
        request: Request,
        employee_no: str = "",
        full_name: str = "",
        ts: str = "",
        sig: str = "",
    ) -> dict[str, Any]:
        employee_no = employee_no.strip()
        full_name = full_name.strip()
        client_ip = resolve_client_ip(request)
        if not employee_no:
            raise HTTPException(status_code=400, detail="employee_no is required")
        error = verify_yanhuang_sso_signature(employee_no, full_name, ts, sig)
        if error:
            raise HTTPException(status_code=401, detail=error)
        conn = get_connection()
        try:
            user = conn.execute("SELECT * FROM sys_user WHERE employee_no = ? LIMIT 1", (employee_no,)).fetchone()
            is_new = user is None
            if is_new:
                display_name = full_name or employee_no
                initial_password = generate_default_temp_password(employee_no)
                conn.execute(
                    """
                    INSERT INTO sys_user (
                        employee_no, username, full_name, password_hash, role, department,
                        must_change_password, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'user', '', 1, ?, ?)
                    """,
                    (employee_no, employee_no, display_name, hash_password(initial_password), now_text(), now_text()),
                )
                conn.commit()
                user = conn.execute("SELECT * FROM sys_user WHERE employee_no = ? LIMIT 1", (employee_no,)).fetchone()
        finally:
            conn.close()
        if is_new:
            record_audit_log("system", "system", "yanhuang_auto_register", "sys_user", employee_no, client_ip)
        # 注意：这里不签发 token，不算登录成功，只是确认账号已经就绪，前端会带着用户名跳去登录页让用户输密码。
        return {"username": user["username"], "is_new": is_new}


if URL_SSO_ENABLED:
    @router.get("/api/auth/sso/url")
    def auth_sso_url(
        request: Request,
        username: str = "",
    ) -> dict[str, Any]:
        """通过 URL 参数 username 自动登录（需开启 DATAMID_URL_SSO_ENABLED）。

        仅用于受信任的内部系统跳转，生产环境建议配合 nginx referer 限制或 DATAMID_URL_SSO_TRUSTED_ORIGINS。
        """
        username = username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="username is required")

        # 可选：校验来源 Origin/Referer
        if URL_SSO_TRUSTED_ORIGINS:
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            matched = any(
                origin.startswith(trusted) or referer.startswith(trusted)
                for trusted in URL_SSO_TRUSTED_ORIGINS
            )
            if not matched:
                raise HTTPException(status_code=403, detail="Untrusted SSO origin")

        client_ip = resolve_client_ip(request)
        conn = get_connection()
        try:
            user = get_user_by_username(conn, username)
            is_new = False
            if user is None and URL_SSO_AUTO_REGISTER:
                display_name = username
                initial_password = generate_default_temp_password(username)
                conn.execute(
                    """
                    INSERT INTO sys_user (
                        employee_no, username, full_name, password_hash, role, department,
                        must_change_password, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'user', '', 1, ?, ?)
                    """,
                    (username, username, display_name, hash_password(initial_password), now_text(), now_text()),
                )
                conn.commit()
                user = get_user_by_username(conn, username)
                is_new = True
                record_audit_log("system", "system", "url_sso_auto_register", "sys_user", username, client_ip)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
        finally:
            conn.close()

        record_audit_log(username, user["role"], "url_sso_login", "sys_user", f"ip={client_ip}", client_ip)
        return {"token": create_access_token(user), "user": serialize_user(user), "is_new": is_new}
