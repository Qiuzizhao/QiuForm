"""账号安全：密码哈希、登录态、CSRF。"""

import functools
import hashlib
import hmac
import secrets

from flask import abort, g, redirect, request, session, url_for

from . import db

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


# ---------------------------------------------------------------- 密码

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
        p=SCRYPT_P, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------- 登录态

def load_current_user():
    """每个请求开始前，把登录用户放进 g。"""
    g.user = None
    user_id = session.get("uid")
    if user_id:
        user = db.get_user(user_id)
        if user:
            g.user = user
        else:
            session.clear()  # 账号被删了，清掉残留的会话


def login_user(user: dict):
    session.clear()
    session["uid"] = user["id"]
    session.permanent = True


def logout_user():
    session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- CSRF

def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def check_csrf():
    """所有表单提交都要带 csrf_token，公开的数据接口除外。"""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.blueprint == "api":
        return  # 数据接口是给外部网页直接调的，不可能带 CSRF
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf")
    if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
        abort(400, description="表单已过期，请刷新页面后重试。")


def init_app(app):
    app.before_request(load_current_user)
    app.before_request(check_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token
