from __future__ import annotations

import base64
from collections import defaultdict
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Any

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.db.connection import get_connection

TOKEN_TTL_SECONDS = int(os.getenv("DATAMID_TOKEN_TTL_SECONDS", "43200"))
TOKEN_SECRET = os.getenv("DATAMID_TOKEN_SECRET", "change-me-in-production")
LOGIN_RATE_WINDOW_SECONDS = int(os.getenv("DATAMID_LOGIN_RATE_WINDOW_SECONDS", "300"))
LOGIN_RATE_MAX_ATTEMPTS = int(os.getenv("DATAMID_LOGIN_RATE_MAX_ATTEMPTS", "5"))
ALLOW_SELF_REGISTER = os.getenv("DATAMID_ALLOW_SELF_REGISTER", "").strip().lower() in {"1", "true", "yes", "on"}
DEV_SSO_TOKEN = os.getenv("DATAMID_DEV_SSO_TOKEN", "").strip()
DEV_SSO_USER = os.getenv("DATAMID_DEV_SSO_USER", "").strip()

# URL 参数单点登录：通过 ?username=xxx 跳转自动登录。默认关闭，需显式开启。
URL_SSO_ENABLED = os.getenv("DATAMID_URL_SSO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
URL_SSO_AUTO_REGISTER = os.getenv("DATAMID_URL_SSO_AUTO_REGISTER", "").strip().lower() in {"1", "true", "yes", "on"}
URL_SSO_TRUSTED_ORIGINS = {
    item.strip()
    for item in os.getenv("DATAMID_URL_SSO_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
}

# 炎黄平台单点登录：只要没配 DATAMID_YANHUANG_SSO_SECRET，这条通道就完全关闭（连路由都不会注册），
# 上线前必须先跟炎黄那边确认好签名方式，双方对不上之前，这条通道保持关闭是安全的默认状态。
YANHUANG_SSO_SECRET = os.getenv("DATAMID_YANHUANG_SSO_SECRET", "").strip()
YANHUANG_SSO_TTL_SECONDS = int(os.getenv("DATAMID_YANHUANG_SSO_TTL_SECONDS", "300"))
TRUSTED_PROXIES = {
    item.strip()
    for item in os.getenv("DATAMID_TRUSTED_PROXIES", "").split(",")
    if item.strip()
}

security = HTTPBearer(auto_error=False)
_login_fails: dict[str, list[float]] = defaultdict(list)
_login_fails_lock = threading.Lock()


def build_login_rate_limit_key(username: str, ip: str) -> str:
    return f"{username.strip().lower()}|{ip.strip()}"


def resolve_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if peer in TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer


def enforce_login_rate_limit(username: str, ip: str) -> None:
    key = build_login_rate_limit_key(username, ip)
    now = time.time()
    with _login_fails_lock:
        hits = [ts for ts in _login_fails[key] if now - ts < LOGIN_RATE_WINDOW_SECONDS]
        _login_fails[key] = hits
        if len(hits) >= LOGIN_RATE_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")


def record_login_failure(username: str, ip: str) -> None:
    key = build_login_rate_limit_key(username, ip)
    now = time.time()
    with _login_fails_lock:
        hits = [ts for ts in _login_fails[key] if now - ts < LOGIN_RATE_WINDOW_SECONDS]
        hits.append(now)
        _login_fails[key] = hits


def clear_login_failures(username: str, ip: str) -> None:
    key = build_login_rate_limit_key(username, ip)
    with _login_fails_lock:
        _login_fails.pop(key, None)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, _ = password_hash.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), password_hash)


def verify_yanhuang_sso_signature(employee_no: str, full_name: str, ts: str, sig: str) -> str:
    """校验炎黄平台跳转过来的签名。返回空字符串表示通过；否则返回失败原因。

    约定的签名方式（炎黄那边需要照这个算，或者我们照他们已有的方式改）：
        sig = HMAC-SHA256(secret, f"{employee_no}|{full_name}|{ts}") 的十六进制摘要
    ts 是 Unix 秒级时间戳，超过 DATAMID_YANHUANG_SSO_TTL_SECONDS（默认 300 秒）视为过期，防止链接被截获后反复使用。
    """
    if not YANHUANG_SSO_SECRET:
        return "炎黄单点登录未启用"
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return "时间戳无效"
    if abs(int(time.time()) - ts_int) > YANHUANG_SSO_TTL_SECONDS:
        return "登录链接已过期，请重新从炎黄平台跳转"
    expected = hmac.new(
        YANHUANG_SSO_SECRET.encode("utf-8"),
        f"{employee_no}|{full_name}|{ts}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not sig or not hmac.compare_digest(sig.strip().lower(), expected):
        return "签名校验失败"
    return ""


def generate_default_temp_password(employee_no: str) -> str:
    """临时密码统一规则：工号 + 工号后三位。炎黄自动注册、管理员重置密码都用这一条规则，
    这是系统内部生成的一次性密码，不需要满足 validate_password_strength 的复杂度要求——
    因为账号会被标记为必须改密码，用户下次登录就会被强制换成真正合规的密码，
    这个临时密码只用来"证明是本人"这一次。"""
    clean = employee_no.strip()
    return clean + clean[-3:]


# 常见弱密码黑名单：即使满足"大小写+数字+符号"的形式要求，这些也太容易被猜到。
_COMMON_WEAK_PASSWORDS = {
    "password1!", "password123!", "qwerty123!", "admin123!", "welcome1!",
    "abc12345!", "p@ssw0rd", "p@ssword1", "passw0rd1!", "12345678!",
    "iloveyou1!", "letmein1!", "monkey123!", "sunshine1!", "changeme1!",
}


def validate_password_strength(password: str, *, username: str = "", employee_no: str = "") -> str:
    """校验密码强度。返回空字符串表示通过；否则返回可直接展示给用户的失败原因。"""
    if len(password) < 8:
        return "密码长度至少 8 位"
    if not re.search(r"[a-z]", password):
        return "密码必须包含小写字母"
    if not re.search(r"[A-Z]", password):
        return "密码必须包含大写字母"
    if not re.search(r"\d", password):
        return "密码必须包含数字"
    if not re.search(r"[^a-zA-Z0-9]", password):
        return "密码必须包含符号（如 !@#$%）"
    if re.search(r"(.)\1\1", password):
        return "密码不能包含 3 个及以上连续重复字符"
    lowered = password.lower()
    if lowered in _COMMON_WEAK_PASSWORDS:
        return "密码过于简单，请更换"
    clean_username = username.strip().lower()
    if clean_username and len(clean_username) >= 3 and clean_username in lowered:
        return "密码不能包含用户名"
    clean_employee_no = employee_no.strip().lower()
    if clean_employee_no and len(clean_employee_no) >= 3 and clean_employee_no in lowered:
        return "密码不能包含工号"
    return ""


def get_user_by_username(conn, username: str):
    return conn.execute("SELECT * FROM sys_user WHERE username = ? OR employee_no = ? LIMIT 1", (username, username)).fetchone()


def _sign_payload(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    sig = hmac.new(TOKEN_SECRET.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{body_b64}.{sig_b64}"


def _decode_signed_token(token: str) -> dict[str, Any]:
    try:
        body_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    expected = base64.urlsafe_b64encode(
        hmac.new(TOKEN_SECRET.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(sig_b64, expected):
        raise HTTPException(status_code=401, detail="Invalid token")
    body_raw = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
    payload = json.loads(body_raw.decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def decode_access_token(token: str) -> dict[str, Any]:
    payload = _decode_signed_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload


def get_current_user(credentials: HTTPAuthorizationCredentials | None = None):
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_access_token(credentials.credentials)
    conn = get_connection()
    try:
        user = conn.execute("SELECT * FROM sys_user WHERE id = ? LIMIT 1", (payload["uid"],)).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    finally:
        conn.close()


def require_admin(user):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def authenticate_user(username: str, password: str):
    conn = get_connection()
    try:
        user = get_user_by_username(conn, username)
        return user if user and verify_password(password, user["password_hash"]) else None
    finally:
        conn.close()


def serialize_user(user) -> dict[str, Any]:
    return {
        "id": user["id"],
        "employee_no": user["employee_no"],
        "username": user["username"],
        "full_name": user["full_name"],
        "role": user["role"],
        "department": user["department"] or "",
        "created_at": user["created_at"],
        "must_change_password": bool(user["must_change_password"]),
    }


def create_access_token(user) -> str:
    return _sign_payload(
        {
            "uid": user["id"],
            "username": user["username"],
            "employee_no": user["employee_no"],
            "role": user["role"],
            "department": user["department"] or "",
            "exp": int(time.time()) + TOKEN_TTL_SECONDS,
            "type": "access",
        }
    )
