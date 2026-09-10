"""新账号的样例任务。

注册成功后自动给这个账号生成一份"多模态课堂互动"样例：两个页面
（学生端 / 教师端）复制进这个任务自己的页面目录，并把页面里写死的
模板任务编号换成新任务的编号 —— 所以每个账号拿到的都是独立任务，
接口指向它自己，不会共用同一个地址。
"""

from pathlib import Path

from . import db, storage

SEED_DIR = Path(__file__).resolve().parent / "seed"

# 模板页面里写死的任务编号，克隆时原样替换成新任务的编号
TEMPLATE_APIID = "2ejeum6zp9k4"

SAMPLE_NAME = "多模态课堂互动 · 全流程测试样例"
SAMPLE_DESC = "学生端：图片 / 音频 / 手绘 / 反应测试；教师端：实时看板。"

# (文件名, 角色)
SAMPLE_PAGES = [("学生端.html", "student"), ("教师端.html", "teacher")]


def available() -> bool:
    """模板文件在不在。"""
    return all((SEED_DIR / name).is_file() for name, _ in SAMPLE_PAGES)


def create_sample_task(user_id: int):
    """给这个用户建一份独立样例任务，返回任务字典。"""
    task = db.create_task(user_id, SAMPLE_NAME, SAMPLE_DESC)
    apiid = task["apiid"]

    for filename, role in SAMPLE_PAGES:
        source = SEED_DIR / filename
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        # 模板里写死的编号 → 这个任务自己的编号
        text = text.replace(TEMPLATE_APIID, apiid)
        # 顺手把页面上写死的 /api/xxx 也改成这个任务的
        text, _ = storage.rewrite_api_refs(text, apiid)
        data = text.encode("utf-8")
        name = storage.save_page_file(apiid, filename, data)
        db.upsert_page(apiid, name, filename, len(data), role=role)

    return task
