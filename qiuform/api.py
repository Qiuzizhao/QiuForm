"""公开数据接口 —— 整个产品的核心。

    POST /api/<apiid>        写入（JSON / 表单 / multipart 带文件）
    GET  /api/<apiid>        读出最近若干条
    GET  /api/<apiid>/all    读出全部
    GET  /api/<apiid>/count  只回条数和最新一条的编号（给大屏轮询用）

地址不鉴权、不加密，谁拿到都能读写——这是为了换取"任何网页、任何大模型
生成的代码，接上就能用"。所以别把敏感信息往这里放。
"""

import json
import threading
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from . import db, multipart, storage
from .urls import base_url

bp = Blueprint("api", __name__, url_prefix="/api")

# 读取限流：按 (任务, 客户端) 记令牌桶。
#
# 为什么不用"最小间隔"：一个班的学生走学校同一个出口 IP，最小间隔会让
# 第二个人打开看板就被拒。这里改成允许突发，既能撑住"全班同时刷新"，
# 又能拦住 setInterval 写死循环那种真正的滥用。
_read_buckets: dict = {}
_read_lock = threading.Lock()


# ---------------------------------------------------------------- 工具

def _ok(payload: dict, status: int = 200):
    body = {"ok": True}
    body.update(payload)
    return jsonify(body), status


def _fail(status: int, code: str, message: str, **extra):
    body = {"ok": False, "error": code, "message": message}
    body.update(extra)
    return jsonify(body), status


def _task_brief(task: dict) -> dict:
    brief = {
        "id": task["apiid"],
        "name": task["name"],
        # 任务一律可读可写；这两个字段保留是为了不破坏已经接好的页面
        "mode": "read_write",
        "readable": True,
        "writable": True,
    }
    page = db.primary_page(task["apiid"])
    if page:
        brief["page"] = f"{base_url()}/p/{task['apiid']}/"
    return brief


def _read_gate_check(apiid: str, bucket: str = "read"):
    """返回需要等待的秒数，0 表示放行。

    bucket 用来区分不同的桶：/all 走 "read"，/count 走 "count"（更宽），
    这样看板频繁轮询计数不会把全量读取的额度用光。
    """
    if bucket == "count":
        burst = float(current_app.config["API_COUNT_BURST"])
        rate = float(current_app.config["API_COUNT_RATE"])
    else:
        burst = float(current_app.config["API_READ_BURST"])
        rate = float(current_app.config["API_READ_RATE"])
    if burst <= 0 or rate <= 0:
        return 0

    key = f"{bucket}|{apiid}|{request.remote_addr}"
    now = time.time()
    with _read_lock:
        tokens, last = _read_buckets.get(key, (burst, now))
        tokens = min(burst, tokens + (now - last) * rate)

        if tokens < 1:
            _read_buckets[key] = (tokens, now)
            return (1 - tokens) / rate

        _read_buckets[key] = (tokens - 1, now)

        # 顺手清一清，避免字典无限增长
        if len(_read_buckets) > 4096:
            for k in [k for k, (tok, ts) in _read_buckets.items()
                      if now - ts > 300 and tok >= burst]:
                _read_buckets.pop(k, None)
    return 0


def _submission_out(item: dict) -> dict:
    out = dict(item["data"])
    out["_id"] = item["id"]
    out["_at"] = item["at"]
    return out


def _not_modified(tag: str):
    """数据没变时回 304：客户端（大屏轮询）省掉整个响应体。"""
    from flask import make_response
    resp = make_response("", 304)
    resp.headers["ETag"] = tag
    return resp


def _etag_matches(header: str, tag: str) -> bool:
    """按 HTTP 规范做"弱比较"。

    经过 Cloudflare 之后，我们发出去的强校验值会被改写成弱校验值
    （W/"4-224"），客户端回来的也带 W/ 前缀；比较时必须忽略这个前缀，
    否则永远匹配不上、304 也就永远不生效。
    """
    if not header:
        return False

    def norm(value: str) -> str:
        value = value.strip()
        return value[2:].strip() if value.startswith("W/") else value

    want = norm(tag)
    for part in header.split(","):
        part = part.strip()
        if part == "*" or norm(part) == want:
            return True
    return False


# ---------------------------------------------------------------- 写入

@bp.route("/<apiid>", methods=["POST", "OPTIONS"])
def submit(apiid: str):
    if request.method == "OPTIONS":
        return ("", 204)

    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")

    content_type = request.headers.get("Content-Type", "")
    uploaded: list = []

    try:
        if multipart.is_multipart(content_type):
            fields, files = multipart.parse(request.get_data(), content_type)
            payload = {k: (v[0] if len(v) == 1 else v) for k, v in fields.items()}
            for item in files:
                if not item["data"]:
                    continue
                ext = Path(item["filename"] or "").suffix.lower()
                if ext not in storage.ALLOWED_UPLOAD_EXT:
                    return _fail(415, "unsupported_file_type",
                                 f"不支持的文件类型：{ext or '无扩展名'}",
                                 allowed=sorted(storage.ALLOWED_UPLOAD_EXT))
                limit = current_app.config["MAX_UPLOAD_BYTES"]
                if len(item["data"]) > limit:
                    return _fail(413, "file_too_large",
                                 f"单个文件不能超过 {limit // 1024 // 1024} MB，"
                                 "建议在页面里先压缩")
                uploaded.append(storage.save_upload(apiid, item["filename"],
                                                    item["data"]))
        elif "application/json" in content_type.lower():
            raw = request.get_data()
            payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        else:
            payload = request.form.to_dict(flat=True)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fail(400, "bad_payload", f"数据解析失败：{exc}")

    if not isinstance(payload, dict):
        payload = {"value": payload}
    if not payload and not uploaded:
        return _fail(400, "empty_payload", "没有收到任何字段")

    if uploaded:
        urls = [url for url, _ in uploaded]
        payload["attachment"] = urls[0] if len(urls) == 1 else urls

    new_id = db.add_submission(apiid, payload)
    body = {"message": "提交成功", "id": new_id}
    if uploaded:
        body["attachments"] = [url for url, _ in uploaded]
    return _ok(body)


# ---------------------------------------------------------------- 读取

@bp.route("/<apiid>", methods=["GET"])
def read_latest(apiid: str):
    limit = current_app.config["API_DEFAULT_LIMIT"]
    asked = request.args.get("limit")
    if asked and asked.isdigit():
        limit = max(1, min(int(asked), 200))
    return _read(apiid, limit=limit, full=False)


@bp.route("/<apiid>/all", methods=["GET", "OPTIONS"])
def read_all(apiid: str):
    if request.method == "OPTIONS":
        return ("", 204)

    # 先判权限再判频率：已关闭的任务不该消耗限流额度，也该直接给出明确原因
    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")

    wait = _read_gate_check(apiid, bucket="read")
    if wait:
        return _fail(429, "too_many_requests",
                     "读取太频繁了，稍等一下再试", retry_after=round(wait, 2))
    return _read(apiid, limit=None, full=True)


@bp.route("/<apiid>/count", methods=["GET", "OPTIONS"])
def read_count(apiid: str):
    """轻量计数：大屏/看板先轮询这里，数字变了再去拉 /all。

    一次 COUNT 加一次 MAX，几百字节的响应；数据多了也不会因为
    "每 5 秒拉一次全量"把带宽和数据库拖住。
    """
    if request.method == "OPTIONS":
        return ("", 204)

    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")

    wait = _read_gate_check(apiid, bucket="count")
    if wait:
        return _fail(429, "too_many_requests",
                     "读取太频繁了，稍等一下再试", retry_after=round(wait, 2))

    total = db.count_submissions(apiid)
    tag = f'"{total}-{db.latest_submission_id(apiid)}"'
    if _etag_matches(request.headers.get("If-None-Match"), tag):
        return _not_modified(tag)

    body = _ok({
        "task": _task_brief(task),
        "total": total,
        "latest_id": db.latest_submission_id(apiid),
        "all_url": f"{base_url()}/api/{apiid}/all",
        "note": f"当前 {total} 条。数字变了再去拉 all_url 取全量。",
    })
    body[0].headers["ETag"] = tag
    return body


def _read(apiid: str, limit, full: bool):
    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")

    total = db.count_submissions(apiid)
    tag = f'"{total}-{db.latest_submission_id(apiid)}"'
    if _etag_matches(request.headers.get("If-None-Match"), tag):
        return _not_modified(tag)

    rows = db.list_submissions(apiid, limit=limit)
    submissions = [_submission_out(row) for row in rows]

    body = {
        "task": _task_brief(task),
        "total": total,
        "count": len(submissions),
        "submissions": submissions,
    }
    if not full:
        body["note"] = (
            f"只返回最近 {len(submissions)} 条。要拿全部数据，请访问 "
            f"{base_url()}/api/{apiid}/all"
        )
    resp = _ok(body)
    resp[0].headers["ETag"] = tag
    return resp


# ---------------------------------------------------------------- CORS

@bp.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    # 允许页面带上 If-None-Match：数据没变时拿 304，省掉整个响应体
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, If-None-Match"
    response.headers["Access-Control-Expose-Headers"] = "ETag"
    response.headers["Access-Control-Max-Age"] = "86400"
    response.headers.setdefault("Cache-Control", "no-store")
    return response
