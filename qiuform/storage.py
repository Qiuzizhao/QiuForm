"""文件存储：任务上传的网页，以及接口收到的附件。"""

import re
import secrets
import shutil
from pathlib import Path

from flask import current_app

from .utils import safe_filename

HTML_EXT = {".html", ".htm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"}

# 接口允许接收的附件类型
ALLOWED_UPLOAD_EXT = IMAGE_EXT | {
    ".pdf", ".txt", ".csv", ".md", ".json",
    ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
}

# 网页可以带上的配套资源（css/js/图片/字体等）
ASSET_EXT = {
    ".css", ".js", ".mjs", ".json", ".map", ".txt", ".xml", ".webmanifest",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".webm", ".wav", ".ogg",
} | HTML_EXT

# 把页面里指向 /api/xxx 的地址改写成本任务的地址
_ABS_API_REF = re.compile(r"""https?://[^\s"'<>()\\]*?/api/[A-Za-z0-9_\-]{4,40}""")
_REL_API_REF = re.compile(r"""(?<![\w:/])/api/[A-Za-z0-9_\-]{4,40}""")


def pages_dir(apiid: str) -> Path:
    """任务的页面目录。只算路径，不创建 —— 读取路径不该有副作用，
    否则删掉的任务会被一次 404 访问重新"长"出空目录来。"""
    return Path(current_app.config["PAGES_DIR"]) / apiid


def files_dir(apiid: str) -> Path:
    return Path(current_app.config["FILES_DIR"]) / apiid


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def rewrite_api_refs(text: str, apiid: str):
    """把网页里写死的接口地址换成本任务的接口地址。

    两种都处理：
      https://某台服务器/api/旧id   →  /api/新id
      /api/旧id                      →  /api/新id
    改成相对地址而不是绝对地址，这样换域名、换端口都不用重新改。
    """
    target = f"/api/{apiid}"
    text, n1 = _ABS_API_REF.subn(target, text)
    text, n2 = _REL_API_REF.subn(target, text)
    return text, n1 + n2


def save_page_file(apiid: str, filename: str, data: bytes) -> str:
    """保存一个网页或它的配套资源，返回实际落盘的文件名。"""
    name = safe_filename(filename, fallback="index.html")
    (_ensure(pages_dir(apiid)) / name).write_bytes(data)
    return name


def delete_page_file(apiid: str, filename: str) -> None:
    target = pages_dir(apiid) / safe_filename(filename)
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def save_upload(apiid: str, filename: str, data: bytes):
    """保存接口收到的附件，返回 (相对 URL, 实际文件名)。

    文件名重新随机生成：避免重名覆盖，也避免客户端塞进 ../ 之类的东西。
    """
    ext = Path(safe_filename(filename)).suffix.lower()
    name = secrets.token_hex(6) + ext
    (_ensure(files_dir(apiid)) / name).write_bytes(data)
    return f"/files/{apiid}/{name}", name


def is_html(filename: str) -> bool:
    return Path(filename).suffix.lower() in HTML_EXT


def is_image(name: str) -> bool:
    return Path(str(name)).suffix.lower() in IMAGE_EXT


def is_upload_path(value) -> bool:
    return isinstance(value, str) and (
        value.startswith("/files/") or value.startswith("/static/")
    )


def remove_task_files(apiid: str) -> None:
    """任务被删除时，把它名下的页面和附件一起清掉，别让磁盘无限长。"""
    for base in (current_app.config["PAGES_DIR"], current_app.config["FILES_DIR"]):
        shutil.rmtree(Path(base) / apiid, ignore_errors=True)
