"""账号：注册、登录、登出、个人设置。"""

import re

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from . import db, security

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fff.-]{2,24}$", re.UNICODE)
MIN_PASSWORD = 6


def _safe_next(target: str):
    """只允许跳回站内路径，避免被拿去做钓鱼跳转。"""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("tasks.index"))

    form = {"username": ""}
    if request.method == "POST":
        form["username"] = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        error = None
        if not USERNAME_RE.match(form["username"]):
            error = "用户名需要 2~24 个字符，可以用中英文、数字、下划线、点和短横线。"
        elif len(password) < MIN_PASSWORD:
            error = f"密码至少 {MIN_PASSWORD} 位。"
        elif password != confirm:
            error = "两次输入的密码不一致。"
        elif db.get_user_by_username(form["username"]):
            error = "这个用户名已经被占用了。"

        if error:
            flash(error, "error")
        else:
            user_id = db.create_user(form["username"],
                                     security.hash_password(password))
            security.login_user({"id": user_id})
            flash("账号创建好了，先去建一个任务吧。", "success")
            return redirect(_safe_next(request.args.get("next")) or url_for("tasks.index"))

    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("tasks.index"))

    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = db.get_user_by_username(username)
        if user and security.verify_password(password, user["password_hash"]):
            security.login_user(user)
            return redirect(_safe_next(request.args.get("next")) or url_for("tasks.index"))
        flash("用户名或密码不对。", "error")

    return render_template("auth/login.html", username=username)


@bp.route("/logout", methods=["POST"])
def logout():
    security.logout_user()
    flash("已退出登录。", "success")
    # 回首页（未登录时首页就是那个极简入口），而不是停在登录页
    return redirect(url_for("tasks.index"))


@bp.route("/account", methods=["GET", "POST"])
@security.login_required
def account():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "password":
            current = request.form.get("current") or ""
            new = request.form.get("new") or ""
            confirm = request.form.get("confirm") or ""
            if not security.verify_password(current, g.user["password_hash"]):
                flash("当前密码不正确。", "error")
            elif len(new) < MIN_PASSWORD:
                flash(f"新密码至少 {MIN_PASSWORD} 位。", "error")
            elif new != confirm:
                flash("两次输入的新密码不一致。", "error")
            else:
                db.update_user_password(g.user["id"], security.hash_password(new))
                session.clear()
                flash("密码已修改，请用新密码重新登录。", "success")
                return redirect(url_for("auth.login"))

        return redirect(url_for("auth.account"))

    user = db.get_user(g.user["id"])
    return render_template("auth/account.html", user=user,
                           stats=db.stats_for_user(g.user["id"]))
