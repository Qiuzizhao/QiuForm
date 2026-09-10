"""SQLite 连接、建表，以及所有数据存取函数。"""

import sqlite3

from flask import current_app, g

from .utils import json_dumps, load_json, new_apiid, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    -- 早期版本有个"显示名称"，后来去掉了，这一列留着兼容旧数据，
    -- 新建账号时统一填成用户名
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apiid       TEXT UNIQUE NOT NULL,
    owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mode        TEXT NOT NULL DEFAULT 'read_write',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks (owner_id);

CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    apiid         TEXT NOT NULL,
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    is_primary    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    UNIQUE (apiid, filename)
);
CREATE INDEX IF NOT EXISTS idx_pages_apiid ON pages (apiid);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    apiid        TEXT NOT NULL,
    data         TEXT NOT NULL,
    submitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_apiid ON submissions (apiid, id DESC);
"""

# 任务的读写模式。第一位可读，第二位可写。
MODES = {
    "read_write": {
        "label": "可读可写",
        "short": "读写",
        "read": True,
        "write": True,
        "hint": "默认。既能收数据，也能让页面把数据读回去做实时统计。",
    },
    "read_only": {
        "label": "只读",
        "short": "只读",
        "read": True,
        "write": False,
        "hint": "数据收完了，但还要继续展示。此时接口不再接受新的提交。",
    },
    "write_only": {
        "label": "只写",
        "short": "只写",
        "read": False,
        "write": True,
        "hint": "适合问卷这类场景：能提交，但读不到别人提交的内容。",
    },
    "closed": {
        "label": "已关闭",
        "short": "关闭",
        "read": False,
        "write": False,
        "hint": "读写全部拒绝。不再使用的任务建议关掉。",
    },
}


def can_read(mode: str) -> bool:
    return bool(MODES.get(mode, {}).get("read"))


def can_write(mode: str) -> bool:
    return bool(MODES.get(mode, {}).get("write"))


def mode_label(mode: str) -> str:
    return MODES.get(mode, {}).get("label", "未知")


# ---------------------------------------------------------------- 连接

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DB_PATH"], timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_db()
    with conn:
        conn.executescript(SCHEMA)


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


# ---------------------------------------------------------------- 账号

def create_user(username: str, password_hash: str) -> int:
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, created_at)"
            " VALUES (?, ?, ?, ?)",
            (username, username, password_hash, now()),
        )
    return cur.lastrowid


def get_user_by_username(username: str):
    row = get_db().execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_user(user_id: int):
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_user_password(user_id: int, password_hash: str):
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )


def stats_for_user(user_id: int) -> dict:
    conn = get_db()
    tasks = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE owner_id = ?", (user_id,)
    ).fetchone()["n"]
    subs = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions s"
        " JOIN tasks t ON t.apiid = s.apiid WHERE t.owner_id = ?",
        (user_id,),
    ).fetchone()["n"]
    return {"tasks": tasks, "submissions": subs}


# ---------------------------------------------------------------- 任务

def create_task(owner_id: int, name: str, description: str = "") -> dict:
    name = (name or "").strip() or "未命名任务"
    description = (description or "").strip()
    conn = get_db()
    with conn:
        for _ in range(30):
            apiid = new_apiid()
            try:
                conn.execute(
                    "INSERT INTO tasks (apiid, owner_id, name, description,"
                    " mode, created_at) VALUES (?, ?, ?, ?, 'read_write', ?)",
                    (apiid, owner_id, name, description, now()),
                )
                return {"apiid": apiid, "name": name, "description": description}
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("生成任务标识失败，请重试")


def get_task(apiid: str):
    row = get_db().execute("SELECT * FROM tasks WHERE apiid = ?", (apiid,)).fetchone()
    return dict(row) if row else None


def get_owned_task(apiid: str, user_id: int):
    """只取属于该用户的任务——权限检查都走这个函数。"""
    row = get_db().execute(
        "SELECT * FROM tasks WHERE apiid = ? AND owner_id = ?", (apiid, user_id)
    ).fetchone()
    return dict(row) if row else None


def list_tasks(user_id: int) -> list:
    rows = get_db().execute(
        """
        SELECT t.*,
               (SELECT COUNT(*) FROM submissions s WHERE s.apiid = t.apiid) AS total,
               (SELECT COUNT(*) FROM pages p WHERE p.apiid = t.apiid) AS page_count
          FROM tasks t
         WHERE t.owner_id = ?
         ORDER BY t.id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_task(apiid: str, user_id: int, name: str, description: str, mode: str) -> bool:
    if mode not in MODES:
        return False
    conn = get_db()
    with conn:
        cur = conn.execute(
            "UPDATE tasks SET name = ?, description = ?, mode = ?"
            " WHERE apiid = ? AND owner_id = ?",
            ((name or "").strip() or "未命名任务",
             (description or "").strip(), mode, apiid, user_id),
        )
    return cur.rowcount > 0


def set_task_mode(apiid: str, user_id: int, mode: str) -> bool:
    if mode not in MODES:
        return False
    conn = get_db()
    with conn:
        cur = conn.execute(
            "UPDATE tasks SET mode = ? WHERE apiid = ? AND owner_id = ?",
            (mode, apiid, user_id),
        )
    return cur.rowcount > 0


def delete_task(apiid: str, user_id: int) -> bool:
    conn = get_db()
    with conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE apiid = ? AND owner_id = ?", (apiid, user_id)
        )
        if cur.rowcount:
            conn.execute("DELETE FROM submissions WHERE apiid = ?", (apiid,))
            conn.execute("DELETE FROM pages WHERE apiid = ?", (apiid,))
    return cur.rowcount > 0


# ---------------------------------------------------------------- 页面

def upsert_page(apiid: str, filename: str, original_name: str, size: int,
                make_primary: bool) -> None:
    conn = get_db()
    with conn:
        existing = conn.execute(
            "SELECT id FROM pages WHERE apiid = ? AND filename = ?", (apiid, filename)
        ).fetchone()
        if make_primary:
            conn.execute("UPDATE pages SET is_primary = 0 WHERE apiid = ?", (apiid,))
        if existing:
            conn.execute(
                "UPDATE pages SET original_name = ?, size = ?,"
                " is_primary = CASE WHEN ? THEN 1 ELSE is_primary END"
                " WHERE apiid = ? AND filename = ?",
                (original_name, size, 1 if make_primary else 0, apiid, filename),
            )
        else:
            conn.execute(
                "INSERT INTO pages (apiid, filename, original_name, size,"
                " is_primary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (apiid, filename, original_name, size,
                 1 if make_primary else 0, now()),
            )


def list_pages(apiid: str) -> list:
    rows = get_db().execute(
        "SELECT * FROM pages WHERE apiid = ? ORDER BY is_primary DESC, id ASC",
        (apiid,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_page(apiid: str, filename: str):
    row = get_db().execute(
        "SELECT * FROM pages WHERE apiid = ? AND filename = ?", (apiid, filename)
    ).fetchone()
    return dict(row) if row else None


def primary_page(apiid: str):
    row = get_db().execute(
        "SELECT * FROM pages WHERE apiid = ? ORDER BY is_primary DESC, id ASC LIMIT 1",
        (apiid,),
    ).fetchone()
    return dict(row) if row else None


def set_primary_page(apiid: str, filename: str) -> bool:
    conn = get_db()
    with conn:
        exists = conn.execute(
            "SELECT 1 FROM pages WHERE apiid = ? AND filename = ?", (apiid, filename)
        ).fetchone()
        if not exists:
            return False
        conn.execute("UPDATE pages SET is_primary = 0 WHERE apiid = ?", (apiid,))
        conn.execute(
            "UPDATE pages SET is_primary = 1 WHERE apiid = ? AND filename = ?",
            (apiid, filename),
        )
    return True


def delete_page(apiid: str, filename: str) -> bool:
    conn = get_db()
    with conn:
        cur = conn.execute(
            "DELETE FROM pages WHERE apiid = ? AND filename = ?", (apiid, filename)
        )
        if cur.rowcount:
            # 删掉的是主页的话，把剩下第一个顶上来
            rest = conn.execute(
                "SELECT filename FROM pages WHERE apiid = ? ORDER BY id LIMIT 1",
                (apiid,),
            ).fetchone()
            if rest:
                conn.execute(
                    "UPDATE pages SET is_primary = 1 WHERE apiid = ? AND filename = ?",
                    (apiid, rest["filename"]),
                )
    return cur.rowcount > 0


# ---------------------------------------------------------------- 提交数据

def add_submission(apiid: str, payload: dict) -> int:
    """整包保存，不做字段校验——这是整个设计的起点。"""
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO submissions (apiid, data, submitted_at) VALUES (?, ?, ?)",
            (apiid, json_dumps(payload), now()),
        )
    return cur.lastrowid


def list_submissions(apiid: str, limit=None, offset=0, newest_first=True) -> list:
    order = "DESC" if newest_first else "ASC"
    sql = (f"SELECT id, data, submitted_at FROM submissions WHERE apiid = ?"
           f" ORDER BY id {order}")
    params: list = [apiid]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
    rows = get_db().execute(sql, params).fetchall()
    out = []
    for row in rows:
        out.append({
            "id": row["id"],
            "at": row["submitted_at"],
            "data": load_json(row["data"]),
        })
    return out


def count_submissions(apiid: str) -> int:
    return get_db().execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE apiid = ?", (apiid,)
    ).fetchone()["n"]


def delete_submission(apiid: str, submission_id: int) -> bool:
    conn = get_db()
    with conn:
        cur = conn.execute(
            "DELETE FROM submissions WHERE apiid = ? AND id = ?",
            (apiid, submission_id),
        )
    return cur.rowcount > 0


def clear_submissions(apiid: str) -> int:
    conn = get_db()
    with conn:
        cur = conn.execute("DELETE FROM submissions WHERE apiid = ?", (apiid,))
    return cur.rowcount


def columns_of(submissions: list) -> list:
    """按首次出现顺序收集字段名——后台不要求提前定义结构。"""
    cols: list = []
    for sub in submissions:
        for key in sub["data"]:
            if key not in cols:
                cols.append(key)
    return cols
