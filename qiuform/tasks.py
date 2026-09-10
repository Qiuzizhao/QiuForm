"""任务的创建、页面托管、数据查看与设置。"""

import csv
import io
import json
from pathlib import Path
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)

from . import db, qrcode_gen, security, storage
from .urls import base_url, task_urls

bp = Blueprint("tasks", __name__)

PAGE_SIZE = 50


def _owned_or_404(apiid: str) -> dict:
    task = db.get_owned_task(apiid, g.user["id"])
    if not task:
        abort(404)
    return task


def _task_urls(apiid: str) -> dict:
    return task_urls(apiid)


# ---------------------------------------------------------------- 首页

@bp.route("/")
def index():
    if not g.user:
        return render_template("landing.html")
    tasks = db.list_tasks(g.user["id"])
    return render_template("tasks/list.html", tasks=tasks,
                           stats=db.stats_for_user(g.user["id"]))


@bp.route("/tasks", methods=["POST"])
@security.login_required
def create():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    task = db.create_task(g.user["id"], name, description)
    flash("任务已创建。把接口地址交给网页，数据就会汇到这里。", "success")
    return redirect(url_for("tasks.overview", apiid=task["apiid"]))


# ---------------------------------------------------------------- 任务详情

@bp.route("/tasks/<apiid>")
@security.login_required
def overview(apiid: str):
    task = _owned_or_404(apiid)
    urls = _task_urls(apiid)
    total = db.count_submissions(apiid)
    recent = db.list_submissions(apiid, limit=5)
    pages = db.list_pages(apiid)
    teacher_page = db.role_page(apiid, "teacher")
    teacher_url = (f"{urls['page']}{quote(teacher_page['filename'])}"
                   if teacher_page else "")
    return render_template(
        "tasks/overview.html", task=task, urls=urls, total=total,
        recent=recent, pages=pages, page_count=len(pages), tab="overview",
        teacher_page=teacher_page,
        teacher_url=teacher_url,
        qr_svg=qrcode_gen.render_svg(urls["page"], scale=5),
        qr_teacher_svg=(qrcode_gen.render_svg(teacher_url, scale=5)
                        if teacher_url else ""),
        upload_max_mb=current_app.config["MAX_UPLOAD_BYTES"] // 1024 // 1024,
        upload_exts=sorted(ext.lstrip(".") for ext in storage.ALLOWED_UPLOAD_EXT),
    )


@bp.route("/tasks/<apiid>/pages")
@security.login_required
def pages(apiid: str):
    task = _owned_or_404(apiid)
    items = db.list_pages(apiid)
    for item in items:
        item["is_html"] = storage.is_html(item["filename"])
    return render_template(
        "tasks/pages.html", task=task, urls=_task_urls(apiid),
        pages=items, page_count=len(items), tab="pages",
        max_page_mb=current_app.config["MAX_PAGE_BYTES"] // 1024 // 1024,
    )


@bp.route("/tasks/<apiid>/pages/upload", methods=["POST"])
@security.login_required
def upload_pages(apiid: str):
    task = _owned_or_404(apiid)
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("请先选择要上传的文件。", "error")
        return redirect(url_for("tasks.pages", apiid=apiid))

    limit = current_app.config["MAX_PAGE_BYTES"]
    # 上传时可以直接指定这个文件是学生端还是教师端
    role = (request.form.get("role") or "").strip()
    if request.form.get("make_primary") == "1":
        role = "student"
    if role not in db.PAGE_ROLES:
        role = ""

    saved, rewritten_total, skipped = [], 0, []
    applied_role, applied_name, role_skipped = "", "", []
    for item in files:
        ext = Path(item.filename).suffix.lower()
        if ext not in storage.ASSET_EXT:
            skipped.append(item.filename)
            continue
        data = item.read()
        if len(data) > limit:
            skipped.append(f"{item.filename}（超过 {limit // 1024 // 1024}MB）")
            continue

        if storage.is_html(item.filename):
            text = data.decode("utf-8", errors="replace")
            text, count = storage.rewrite_api_refs(text, apiid)
            rewritten_total += count
            data = text.encode("utf-8")

        name = storage.save_page_file(apiid, item.filename, data)
        # 所有文件都要登记，否则 css/js/图片这些配套资源访问不到
        # 角色只给这次上传的第一个 HTML：一次传多个 HTML 时，
        # 谁当学生端 / 教师端没法猜，剩下的可以在页面列表里单独设
        file_role = ""
        auto_student = True
        if role and storage.is_html(name):
            if not applied_role:
                file_role, applied_role, applied_name = role, role, name
            else:
                role_skipped.append(name)
                auto_student = False     # 别偷偷又塞给学生端
        db.upsert_page(apiid, name, item.filename, len(data),
                       role=file_role, auto_student=auto_student)
        saved.append(name)

    if saved:
        if any(storage.is_html(n) for n in saved):
            flash(f"已上传 {len(saved)} 个文件。", "success")
        else:
            flash(f"已上传 {len(saved)} 个配套资源。", "success")
        if applied_role:
            flash(f"已把{db.PAGE_ROLES[applied_role]}指向 {applied_name}。", "success")
        if role_skipped:
            flash("这次一起传了多个 HTML，只有第一个设成了"
                  f"{db.PAGE_ROLES[applied_role]}；"
                  f"{'、'.join(role_skipped)} 可以在下面的列表里单独设。",
                  "info")
        if rewritten_total:
            flash(f"顺手把页面里 {rewritten_total} 处接口地址改写成了本任务的地址。",
                  "info")
    if skipped:
        flash("以下文件被跳过：" + "、".join(skipped), "error")
    return redirect(url_for("tasks.pages", apiid=apiid))


@bp.route("/tasks/<apiid>/pages/<path:filename>/role", methods=["POST"])
@bp.route("/tasks/<apiid>/pages/<path:filename>/primary", methods=["POST"])
@security.login_required
def set_page_role(apiid: str, filename: str):
    _owned_or_404(apiid)
    if not storage.is_html(filename):
        flash("只有 HTML 文件能设成学生端或教师端。", "error")
        return redirect(url_for("tasks.pages", apiid=apiid))

    role = (request.form.get("role") or "").strip()
    if role not in db.PAGE_ROLES:
        role = ""
    if db.set_page_role(apiid, filename, role):
        if role:
            flash(f"已把 {filename} 设为{db.PAGE_ROLES[role]}。", "success")
        else:
            flash(f"已取消 {filename} 的角色。", "success")
    else:
        flash("找不到这个文件。", "error")
    return redirect(url_for("tasks.pages", apiid=apiid))


@bp.route("/tasks/<apiid>/pages/<path:filename>/delete", methods=["POST"])
@security.login_required
def delete_page(apiid: str):
    _owned_or_404(apiid)
    if db.delete_page(apiid, filename):
        storage.delete_page_file(apiid, filename)
        flash(f"已删除 {filename}。", "success")
    else:
        flash("找不到这个文件。", "error")
    return redirect(url_for("tasks.pages", apiid=apiid))


@bp.route("/tasks/<apiid>/data")
@security.login_required
def data(apiid: str):
    task = _owned_or_404(apiid)
    page_no = max(1, request.args.get("page", type=int) or 1)
    total = db.count_submissions(apiid)
    rows = db.list_submissions(apiid, limit=PAGE_SIZE,
                               offset=(page_no - 1) * PAGE_SIZE)
    columns = db.columns_of(list(reversed(rows))) if rows else []
    if "attachment" in columns:
        columns.remove("attachment")
        columns.insert(0, "attachment")
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template(
        "tasks/data.html", task=task, urls=_task_urls(apiid), tab="data",
        rows=rows, columns=columns, total=total, page_no=page_no,
        total_pages=total_pages, page_size=PAGE_SIZE,
        page_count=len(db.list_pages(apiid)),
    )


@bp.route("/tasks/<apiid>/data/<int:submission_id>/delete", methods=["POST"])
@security.login_required
def delete_submission(apiid: str, submission_id: int):
    _owned_or_404(apiid)
    db.delete_submission(apiid, submission_id)
    flash("已删除这条记录。", "success")
    return redirect(url_for("tasks.data", apiid=apiid))


@bp.route("/tasks/<apiid>/data/clear", methods=["POST"])
@security.login_required
def clear_data(apiid: str):
    _owned_or_404(apiid)
    count = db.clear_submissions(apiid)
    flash(f"已清空 {count} 条提交记录。", "success")
    return redirect(url_for("tasks.data", apiid=apiid))


@bp.route("/tasks/<apiid>/data.csv")
@security.login_required
def export_csv(apiid: str):
    task = _owned_or_404(apiid)
    rows = list(reversed(db.list_submissions(apiid)))
    columns = db.columns_of(rows)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["提交时间"] + columns)
    for item in rows:
        line = [item["at"]]
        for col in columns:
            value = item["data"].get(col, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            line.append(value)
        writer.writerow(line)

    body = buf.getvalue().encode("utf-8-sig")   # BOM：Excel 打开中文不乱码
    filename = f"{task['name']}-{apiid}.csv"
    return _download(body, filename, "text/csv; charset=utf-8")


@bp.route("/tasks/<apiid>/data.json")
@security.login_required
def export_json(apiid: str):
    task = _owned_or_404(apiid)
    rows = db.list_submissions(apiid)
    payload = {
        "task": {"id": apiid, "name": task["name"],
                 "description": task["description"], "mode": task["mode"]},
        "exported_from": base_url(),
        "total": len(rows),
        "submissions": [{"_id": r["id"], "_at": r["at"], **r["data"]} for r in rows],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return _download(body, f"{task['name']}-{apiid}.json", "application/json")


def _download(body: bytes, filename: str, content_type: str):
    from urllib.parse import quote
    from flask import Response
    quoted = quote(filename)
    return Response(
        body, mimetype=content_type,
        headers={"Content-Disposition":
                 f"attachment; filename=\"data\"; filename*=UTF-8''{quoted}"},
    )


@bp.route("/tasks/<apiid>/settings")
@security.login_required
def settings(apiid: str):
    task = _owned_or_404(apiid)
    return render_template(
        "tasks/settings.html", task=task, urls=_task_urls(apiid), tab="settings",
        modes=db.MODES, page_count=len(db.list_pages(apiid)),
    )


@bp.route("/tasks/<apiid>/settings", methods=["POST"])
@security.login_required
def save_settings(apiid: str):
    _owned_or_404(apiid)
    action = request.form.get("action", "save")

    if action == "mode":
        mode = request.form.get("mode") or ""
        if db.set_task_mode(apiid, g.user["id"], mode):
            flash(f"读写模式已切换为「{db.mode_label(mode)}」。", "success")
        else:
            flash("读写模式不合法。", "error")
    else:
        name = request.form.get("name") or ""
        description = request.form.get("description") or ""
        mode = request.form.get("mode") or "read_write"
        if db.update_task(apiid, g.user["id"], name, description, mode):
            flash("设置已保存。", "success")
        else:
            flash("保存失败。", "error")
    return redirect(url_for("tasks.settings", apiid=apiid))


@bp.route("/tasks/<apiid>/delete", methods=["POST"])
@security.login_required
def delete_task(apiid: str):
    task = _owned_or_404(apiid)
    if (request.form.get("confirm") or "").strip() != task["name"]:
        flash("任务名没对上，没有删除。", "error")
        return redirect(url_for("tasks.settings", apiid=apiid))
    db.delete_task(apiid, g.user["id"])
    storage.remove_task_files(apiid)
    flash(f"任务「{task['name']}」已删除。", "success")
    return redirect(url_for("tasks.index"))


@bp.route("/tasks/<apiid>/qr.svg")
@security.login_required
def qr_svg(apiid: str):
    _owned_or_404(apiid)
    which = request.args.get("target", "page")
    urls = _task_urls(apiid)
    from flask import Response

    if which == "both":
        # 学生端 + 教师端一起下载，一张图两个码
        items = [(urls["page"], "学生端")]
        teacher = db.role_page(apiid, "teacher")
        if teacher:
            items.append((urls["page"] + quote(teacher["filename"]), "教师端"))
        svg = (qrcode_gen.render_pair_svg(items, scale=8) if len(items) > 1
               else qrcode_gen.render_svg(items[0][0], scale=8))
    else:
        data = urls["page"] if which == "page" else urls["api"]
        svg = qrcode_gen.render_svg(data, scale=8)

    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
