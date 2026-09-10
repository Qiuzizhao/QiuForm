#!/usr/bin/env python3
"""往某个任务的接口里灌一批演示数据。

    python examples/seed_demo.py <接口编号> [服务器地址]
"""

import json
import random
import sys
import urllib.error
import urllib.request

apiid = sys.argv[1] if len(sys.argv) > 1 else ""
base = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000").rstrip("/")

if not apiid:
    print("用法：python examples/seed_demo.py <接口编号> [服务器地址]")
    raise SystemExit(1)

NAMES = ["张三", "李四", "王五", "赵六", "钱七", "孙八", "周九", "吴十",
         "郑一", "王二", "冯三", "陈四", "褚五", "卫六", "蒋七", "沈八",
         "韩九", "杨十", "朱十一", "秦十二"]
LEVELS = ["完全掌握", "基本掌握", "还没听懂"]
COMMENTS = [
    "知道了光照会影响发芽率", "学会了给传感器接线", "原来湿度也要控制",
    "小组分工还要再明确一点", "想让我的小苗长得更高", "数据记录要更及时",
    "明白了为什么要做对照实验", "",
]

random.seed(20260910)
ok = 0
for i, name in enumerate(NAMES):
    record = {
        "姓名": name,
        "掌握程度": random.choices(LEVELS, weights=[.5, .35, .15])[0],
        "计时秒数": random.randint(45, 210),
        "一句话收获": random.choice(COMMENTS),
    }
    if i == 4:
        record["想再试试"] = True
    if i == 9:
        record["建议"] = "希望多留点动手时间"

    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/{apiid}", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        if urllib.request.urlopen(req, timeout=10).status == 200:
            ok += 1
    except urllib.error.HTTPError as exc:
        print("失败：", exc.code, exc.read().decode("utf-8", "replace")[:120])
        break

print(f"已提交 {ok} 条 → {base}/api/{apiid}/all")
