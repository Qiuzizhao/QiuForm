"""对外地址的生成。

放在反向代理后面时，WSGI 收到的 host 和 scheme 是内网的
（比如 http://127.0.0.1:8000），直接拿它拼接口地址会让二维码指向
本机。所以要么开 TRUST_PROXY 从 X-Forwarded-* 还原，要么直接用
QIUFORM_BASE_URL 钉死对外地址。
"""

from flask import current_app, request


def base_url() -> str:
    """对外可访问的站点根地址，不带结尾斜杠。"""
    configured = current_app.config.get("BASE_URL")
    if configured:
        return configured.rstrip("/")
    return request.host_url.rstrip("/")


def task_urls(apiid: str) -> dict:
    host = base_url()
    return {
        "api": f"{host}/api/{apiid}",
        "api_all": f"{host}/api/{apiid}/all",
        "page": f"{host}/p/{apiid}/",
        "page_path": f"/p/{apiid}/",
    }
