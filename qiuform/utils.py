"""通用小工具。"""

import json
import re
import secrets
import socket
import string
import unicodedata
from datetime import datetime
from pathlib import Path

APIID_ALPHABET = string.ascii_lowercase + string.digits
APIID_LENGTH = 12

_UNSAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)


def now() -> str:
    """统一的本地时间格式，存库和展示都用它。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def new_apiid() -> str:
    """任务的公开标识。去掉容易看混的字符，方便口头念、手动敲。"""
    alphabet = APIID_ALPHABET.replace("l", "").replace("o", "").replace("0", "")
    return "".join(secrets.choice(alphabet) for _ in range(APIID_LENGTH))


def new_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)[:length]


def safe_filename(name: str, fallback: str = "file") -> str:
    """把上传的文件名洗成一个安全的、可以直接拼路径的名字。

    保留中文和常见字符，去掉路径分隔符与 .. 之类的东西。
    """
    name = unicodedata.normalize("NFC", Path(str(name or "")).name)
    name = name.replace("\\", "/").split("/")[-1]
    name = name.strip().strip(".")
    name = _UNSAFE_NAME.sub("_", name)
    if not name:
        return fallback
    return name[:120]


def human_size(num) -> str:
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def load_json(text, default=None):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {} if default is None else default


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
