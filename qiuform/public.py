"""公开访问：任务上传的网页，以及接口收到的附件。"""

from pathlib import Path

from flask import (Blueprint, abort, current_app, redirect, render_template,
                   send_file, url_for)

from . import db, storage

bp = Blueprint("public", __name__)


def _resolve(folder: Path, filename: str) -> Path:
    """把请求里的文件名解析成目录内的真实文件，挡住路径穿越。"""
    folder = folder.resolve()
    target = (folder / filename).resolve()
    if target != folder and folder not in target.parents:
        abort(404)
    return target


@bp.route("/p/<apiid>/")
def page_root(apiid: str):
    task = db.get_task(apiid)
    if not task:
        abort(404)
    page = db.primary_page(apiid)
    if not page:
        return render_template("public/no_page.html", task=task), 404
    return redirect(url_for("public.page_file", apiid=apiid,
                            filename=page["filename"]))


@bp.route("/p/<apiid>/<path:filename>")
def page_file(apiid: str, filename: str):
    task = db.get_task(apiid)
    if not task:
        abort(404)
    # 只有登记过的文件才对外服务
    if not db.get_page(apiid, filename):
        abort(404)
    target = _resolve(storage.pages_dir(apiid), filename)
    if not target.is_file():
        abort(404)
    response = send_file(target, conditional=True)
    if target.suffix.lower() in storage.HTML_EXT:
        response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/files/<apiid>/<path:filename>")
def uploaded_file(apiid: str, filename: str):
    target = _resolve(storage.files_dir(apiid), filename)
    if not target.is_file():
        abort(404)
    response = send_file(target, conditional=True, max_age=3600)
    return response


@bp.route("/p/<apiid>")
def page_root_noslash(apiid: str):
    return redirect(url_for("public.page_root", apiid=apiid))
