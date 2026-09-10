#!/usr/bin/env python3
"""验证反向代理下的对外地址生成。

这是部署时最容易出错的地方：站点跑在 nginx 后面，如果没还原
X-Forwarded-*，生成的接口地址会写成 http://127.0.0.1:8000/...，
二维码也会指向本机。

    python tests/check_deploy_urls.py

需要库里已经有一个任务（用 data/qiuform.db 里的第一个任务）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".vendor"))

from qiuform import create_app  # noqa: E402

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}   {detail}")


PROXY_HEADERS = {
    "Host": "qiuform.qiuzizhao.com",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "qiuform.qiuzizhao.com",
    "X-Forwarded-For": "203.0.113.9",
}


def probe(config, apiid):
    app = create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get(f"/api/{apiid}", headers=PROXY_HEADERS)
        if resp.status_code != 200:
            return None
        data = resp.get_json()
        return {
            "note": data.get("note", ""),
            "page": (data.get("task") or {}).get("page", ""),
        }


def main():
    from qiuform.db import get_db

    app = create_app()
    with app.app_context():
        row = get_db().execute(
            "SELECT apiid FROM tasks ORDER BY id LIMIT 1"
        ).fetchone()
    if not row:
        print("库里还没有任务，先在站点上建一个再跑这个检查。")
        return 1
    apiid = row["apiid"]
    print(f"\n用任务 {apiid} 检查对外地址生成\n")

    print("1. 没开 TRUST_PROXY、也没设 BASE_URL")
    plain = probe({"TRUST_PROXY": False, "BASE_URL": None}, apiid)
    # 域名会跟着 Host 头走（nginx 传了 $host），但协议是内网的 http，
    # 生成的二维码会指向 http:// 而不是 https://
    check("域名正确但协议退化成 http（这就是要修的问题）",
          plain and "http://qiuform.qiuzizhao.com" in plain["note"]
          and "https://" not in plain["note"], plain)

    print("\n2. 打开 TRUST_PROXY")
    proxied = probe({"TRUST_PROXY": True, "BASE_URL": None}, apiid)
    check("识别出真实域名", proxied and "qiuform.qiuzizhao.com" in proxied["note"],
          proxied)
    check("协议还原成 https", proxied and "https://qiuform.qiuzizhao.com" in proxied["note"],
          proxied)
    check("二维码用的页面地址也跟着对了",
          proxied and proxied["page"].startswith("https://qiuform.qiuzizhao.com/p/"),
          proxied)

    print("\n3. 直接钉死 BASE_URL（不依赖请求头）")
    pinned = probe({"TRUST_PROXY": False,
                    "BASE_URL": "https://qiuform.qiuzizhao.com"}, apiid)
    check("即使没有转发头也生成正确地址",
          pinned and "https://qiuform.qiuzizhao.com/api/" in pinned["note"], pinned)

    print(f"\n结果：{passed} 项通过，{failed} 项失败\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
