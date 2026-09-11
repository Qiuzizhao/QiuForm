"""QForm —— 给任意网页配一个数据接口。

产品的核心只有一句话：一个任务 = 一个公开的读写接口地址。
围绕它再长出账号、页面托管、数据查看这些外围能力。
"""

import os
import secrets
import time
import hashlib
from datetime import timedelta
from pathlib import Path

from flask import Flask, current_app, jsonify, render_template, request
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


def _asset_version(app: Flask) -> str:
    """静态资源的版本号，取内容的哈希。

    更新部署后，浏览器和 CDN 很可能还在用旧的 css / js。把内容哈希拼在
    资源地址后面，内容一变地址就变，缓存自动失效。这个坑真踩过一次：
    新 HTML 配旧 CSS，页面在本地正常、线上却乱了。
    """
    digest = hashlib.sha256()
    static_dir = Path(app.static_folder or "")
    for name in sorted(["app.css", "app.js"]):
        path = static_dir / name
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


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

    app.config["ASSET_V"] = _asset_version(app)
    app.jinja_env.globals["ASSET_V"] = app.config["ASSET_V"]

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
    app.jinja_env.globals["SITE_NAME"] = "QForm"
    # 模板里也要用对外地址，不能直接拿 request.host_url ——
    # 反向代理之后那是内网的 http 地址
    from .urls import base_url
    app.jinja_env.globals["base_url"] = base_url

    @app.context_processor
    def inject_globals():
        from flask import g
        return {"current_user": getattr(g, "user", None), "now_ts": int(time.time())}

    @app.after_request
    def cache_headers(response):
        """给静态资源和任务页面配上合适的缓存策略。

        静态资源的地址带内容哈希（?v=…），换了内容地址就变，所以可以放心
        让浏览器和 CDN 长期缓存 —— 之前这里是 no-cache，每次访问都要回源
        校验一次，白白多花一个来回。
        任务的页面 / 附件文件名不带哈希，只能给短一点的缓存。
        """
        path = request.path
        if 200 <= response.status_code < 300:
            if path.startswith("/static/") or path.startswith("/files/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.startswith("/p/"):
                html = (response.mimetype or "").startswith("text/html")
                response.headers["Cache-Control"] = (
                    "public, max-age=60" if html else "public, max-age=3600")
        return response

    @app.after_request
    def early_hints(response):
        """HTML 响应带上 preload 提示，Cloudflare 会转成 103 Early Hints 先发给浏览器。

        HTML 首字节要等一个往返（经 Cloudflare 约 130~260ms），这期间浏览器
        本来只能干等；有了 103，它可以在我们回主体的同时就开始下 CSS/JS。
        只给站内页面加 —— 任务页面（/p/）是独立页面，不需要这两个文件。
        """
        if (response.status_code == 200
                and (response.mimetype or "").startswith("text/html")
                and not request.path.startswith(("/p/", "/files/"))):
            v = current_app.config["ASSET_V"]
            response.headers["Link"] = (
                f"</static/app.css?v={v}>; rel=preload; as=style, "
                f"</static/app.js?v={v}>; rel=preload; as=script"
            )
        return response

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
