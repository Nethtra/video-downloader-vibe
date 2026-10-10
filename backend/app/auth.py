"""注册 / 登录 / 会话认证。

- 邮箱+密码注册（无邮箱验证），密码 scrypt 加盐哈希（标准库，零依赖）
- 会话：随机 token 存 SQLite sessions 表，HttpOnly cookie 下发（服务端可吊销）
- SameSite=Lax：Stripe Checkout 支付完成后顶层跳回站点会携带 cookie
"""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from . import db

COOKIE_NAME = "sid"
SESSION_DAYS = 30
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


# ---------- 密码 ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, h_hex = stored.split("$")
        h = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(h.hex(), h_hex)  # 常量时间比较


# ---------- 注册 / 登录 ----------

def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _valid_email(email: str) -> bool:
    return "@" in email and 1 < len(email) <= 254 and " " not in email


def register(email: str, password: str):
    """注册成功返回用户行；参数不合法/邮箱重复抛 ValueError（错误码见消息）。"""
    email = _norm_email(email)
    if not _valid_email(email):
        raise ValueError("bad_email")
    if not password or len(password) < 8:
        raise ValueError("weak_password")
    try:
        db.create_user(email, hash_password(password))
    except sqlite3.IntegrityError:
        raise ValueError("email_taken")
    return db.get_user_by_email(email)


def login(email: str, password: str):
    """验证通过返回 (user, token)；凭证错误抛 ValueError('bad_credentials')。"""
    email = _norm_email(email)
    user = db.get_user_by_email(email) if _valid_email(email) else None
    if not user or not verify_password(password or "", user["password_hash"]):
        raise ValueError("bad_credentials")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    db.create_session(token, user["id"], expires)
    return user, token


def logout(token: str) -> None:
    if token:
        db.delete_session(token)


# ---------- 会话辅助 ----------

def current_user(request: Request):
    """从 cookie 还原登录用户；未登录/会话过期返回 None（无副作用）。"""
    return db.get_session_user(request.cookies.get(COOKIE_NAME) or "")


def is_vip(user) -> bool:
    if not user or not user["vip_until"]:
        return False
    try:
        return datetime.fromisoformat(user["vip_until"]) > datetime.now(timezone.utc)
    except ValueError:
        return False


def user_payload(user) -> dict:
    """统一的用户信息响应结构。"""
    return {"email": user["email"], "vip_until": user["vip_until"],
            "is_vip": is_vip(user)}


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax",
        secure=os.getenv("COOKIE_SECURE") == "1", path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def start_session(user, response: Response) -> dict:
    """注册/登录成功后的公共收尾：建会话 + 下发 cookie + 返回用户信息。"""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    db.create_session(token, user["id"], expires)
    set_auth_cookie(response, token)
    return user_payload(user)
