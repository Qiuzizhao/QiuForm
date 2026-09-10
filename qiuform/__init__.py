"""QiuForm —— 给任意网页配一个数据接口。

产品的核心只有一句话：一个任务 = 一个公开的读写接口地址。
围绕它再长出账号、页面托管、数据查看这些外围能力。
"""

import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, security
from .utils import human_size

__version__ = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent


def _load_secret(data_dir: Path) -> str:
    """密钥持久化到数据目录，重启后登录态不失效。"""
    env = os.environ.get("QIUFORM_SECRET_KEY")
    if env:
        return env
    path = data_dir / "secret_key"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def create_app(config: dict = None) -> Flask:
    app = Flask(__name__)

    data_dir = Path(os.environ.get("QIUFORM_DATA_DIR", ROOT / "data")).resolve()

    def env_flag(name: str) -> bool:
        return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")

    app.config.update(
        DATA_DIR=data_dir,
        DB_PATH=data_dir / "qiuform.db",
        PAGES_DIR=data_dir / "pages",
        FILES_DIR=data_dir / "files",
        SECRET_KEY=_load_secret(data_dir),
        # 放在 nginx / Cloudflare 后面时打开，从 X-Forwarded-* 还原真实主机与协议
        TRUST_PROXY=env_flag("QIUFORM_TRUST_PROXY"),
        # 钉死对外地址（可选）。设了它就不依赖请求头，二维码和复制出来的
        # 接口地址一定是这个域名。
        BASE_URL=(os.environ.get("QIUFORM_BASE_URL") or "").strip() or None,
        # 只在 HTTPS 站点上打开：让会话 Cookie 不被明文发送
        SESSION_COOKIE_SECURE=env_flag("QIUFORM_SECURE_COOKIE"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=48 * 1024 * 1024,
        MAX_PAGE_BYTES=4 * 1024 * 1024,       # 单个网页文件
        MAX_UPLOAD_BYTES=8 * 1024 * 1024,     # 接口收到的单个附件
        API_DEFAULT_LIMIT=3,                  # 不带 /all 时返回几条
        # 读取限流：允许突发 N 次，之后每秒补充 R 次（按 任务+客户端IP 计）
        # 放宽到能撑住"一个班同时刷新看板"，同时拦住写死循环的刷新
        API_READ_BURST=float(os.environ.get("QIUFORM_READ_BURST", "20")),
        API_READ_RATE=float(os.environ.get("QIUFORM_READ_RATE", "5")),
    )
    if config:
        app.config.update(config)

    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1
        )

    for folder in (app.config["DATA_DIR"], app.config["PAGES_DIR"],
                   app.config["FILES_DIR"]):
        Path(folder).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    security.init_app(app)

    # 接口返回可读的中文，而不是 \u4e00 这种东西 ——
    # 老师会直接把这段输出复制给大模型当数据样例。
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    from . import api, auth, public, tasks

    app.register_blueprint(auth.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(public.bp)
    app.register_blueprint(api.bp)

    app.jinja_env.filters["human_size"] = human_size
    app.jinja_env.globals["SITE_NAME"] = "QiuForm"
    app.jinja_env.globals["mode_label"] = db.mode_label
    app.jinja_env.globals["MODES"] = db.MODES
    # 模板里也要用对外地址，不能直接拿 request.host_url ——
    # 反向代理之后那是内网的 http 地址
    from .urls import base_url
    app.jinja_env.globals["base_url"] = base_url

    @app.context_processor
    def inject_globals():
        from flask import g
        return {"current_user": getattr(g, "user", None), "now_ts": int(time.time())}

    @app.errorhandler(400)
    def bad_request(exc):
        return _error_page(exc, 400, "请求有问题",
                           getattr(exc, "description", "表单可能已过期，刷新后重试。")), 400

    @app.errorhandler(403)
    def forbidden(_exc):
        return _error_page(None, 403, "没有权限", "这个页面不属于你，或者需要登录。"), 403

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "not_found",
                            "message": "任务不存在"}), 404
        return _error_page(None, 404, "找不到页面",
                           "地址可能写错了，或者内容已经被删除。"), 404

    @app.errorhandler(413)
    def too_large(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "too_large",
                            "message": "请求体太大"}), 413
        return _error_page(None, 413, "文件太大",
                           "上传的内容超过了服务器允许的大小。"), 413

    return app


def _error_page(_exc, code, title, message) -> str:
    return render_template("error.html", code=code, title=title, message=message)
