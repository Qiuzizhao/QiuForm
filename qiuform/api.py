"""公开数据接口 —— 整个产品的核心。

    POST /api/<apiid>        写入（JSON / 表单 / multipart 带文件）
    GET  /api/<apiid>        读出最近若干条
    GET  /api/<apiid>/all    读出全部

地址不鉴权、不加密，谁拿到都能读写——这是为了换取"任何网页、任何大模型
生成的代码，接上就能用"。风险由任务的读写模式来兜底。
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
        "mode": task["mode"],
        "readable": db.can_read(task["mode"]),
        "writable": db.can_write(task["mode"]),
    }
    page = db.primary_page(task["apiid"])
    if page:
        brief["page"] = f"{base_url()}/p/{task['apiid']}/"
    return brief


def _read_gate_check(apiid: str):
    """返回需要等待的秒数，0 表示放行。"""
    burst = float(current_app.config["API_READ_BURST"])
    rate = float(current_app.config["API_READ_RATE"])
    if burst <= 0 or rate <= 0:
        return 0

    key = f"{apiid}|{request.remote_addr}"
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


# ---------------------------------------------------------------- 写入

@bp.route("/<apiid>", methods=["POST", "OPTIONS"])
def submit(apiid: str):
    if request.method == "OPTIONS":
        return ("", 204)

    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")
    if not db.can_write(task["mode"]):
        return _fail(403, "read_only",
                     f"该任务当前是「{db.mode_label(task['mode'])}」，不接受新的提交")

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
    if not db.can_read(task["mode"]):
        return _fail(403, "write_only",
                     f"该任务当前是「{db.mode_label(task['mode'])}」，不允许读取")

    wait = _read_gate_check(apiid)
    if wait:
        return _fail(429, "too_many_requests",
                     "读取太频繁了，稍等一下再试", retry_after=round(wait, 2))
    return _read(apiid, limit=None, full=True)


def _read(apiid: str, limit, full: bool):
    task = db.get_task(apiid)
    if not task:
        return _fail(404, "task_not_found", "任务不存在")
    if not db.can_read(task["mode"]):
        return _fail(403, "write_only",
                     f"该任务当前是「{db.mode_label(task['mode'])}」，不允许读取")

    total = db.count_submissions(apiid)
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
    return _ok(body)


# ---------------------------------------------------------------- CORS

@bp.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "86400"
    response.headers.setdefault("Cache-Control", "no-store")
    return response
