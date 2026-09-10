#!/usr/bin/env python3
"""QForm 端到端冒烟测试。

先把站点跑起来，再执行：
    python run.py --port 8000
    python tests/smoke_test.py http://127.0.0.1:8000

测试会自己注册一个新账号、走完创建任务/接入数据/上传页面/导出/改设置的
完整流程，最后把测试任务删掉。
"""

import http.cookiejar
import concurrent.futures
import json
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

passed = 0
failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}   {detail}")


class Client:
    """带 cookie 的极简 HTTP 客户端。"""

    def __init__(self, base=BASE):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def raw(self, method, path, *, body=None, headers=None):
        url = path if path.startswith("http") else self.base + path
        # 带上 User-Agent：默认的 "Python-urllib/x.y" 会被 Cloudflare
        # 的机器人规则拦成 403（curl、浏览器、python-requests 都不拦）
        merged = {"User-Agent": "QiuForm-SmokeTest/1.0 (+local)"}
        if headers:
            merged.update(headers)
        req = urllib.request.Request(url, data=body, headers=merged,
                                     method=method)
        try:
            resp = self.opener.open(req, timeout=20)
            return resp.status, resp.read(), resp.geturl(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.geturl(), dict(exc.headers)
        except urllib.error.URLError as exc:
            # 网络抖动（DNS、TLS 中断等）不该让整个测试崩溃，
            # 让它变成一次"失败"更合理
            body = json.dumps(
                {"ok": False, "message": f"网络错误：{exc.reason}"}
            ).encode("utf-8")
            return 0, body, str(url), {}

    def get(self, path):
        status, body, url, headers = self.raw("GET", path)
        return status, body.decode("utf-8", "replace"), url, headers

    def load_csrf(self, path="/tasks"):
        _, html, _, _ = self.get(path)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match:
            self.csrf = match.group(1)
        return self.csrf

    def post(self, path, fields, *, files=(), headers=None):
        if files:
            body, ctype = build_multipart(fields, files)
        else:
            body = urllib.parse.urlencode(fields).encode("utf-8")
            ctype = "application/x-www-form-urlencoded"
        merged = {"Content-Type": ctype}
        if headers:
            merged.update(headers)
        status, raw, url, resp_headers = self.raw("POST", path, body=body,
                                                  headers=merged)
        return status, raw.decode("utf-8", "replace"), url, resp_headers

    def post_form(self, path, fields):
        fields = dict(fields)
        fields.setdefault("csrf_token", self.csrf)
        return self.post(path, fields)


def build_multipart(fields, files=(), boundary="----QiuFormTestBoundary"):
    parts = []
    for key, value in fields.items():
        parts.append(b"--" + boundary.encode())
        parts.append(b'Content-Disposition: form-data; name="%s"' % key.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    for name, filename, ctype, data in files:
        parts.append(b"--" + boundary.encode())
        parts.append(
            b'Content-Disposition: form-data; name="%s"; filename="%s"'
            % (name.encode(), filename.encode("utf-8")))
        parts.append(b"Content-Type: " + ctype.encode())
        parts.append(b"")
        parts.append(data)
    parts.append(b"--" + boundary.encode() + b"--")
    parts.append(b"")
    return b"\r\n".join(parts), "multipart/form-data; boundary=" + boundary


def make_png(size=4, rgb=(40, 120, 220)):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


def main():
    stamp = str(int(time.time()))[-6:]
    user = f"tester{stamp}"
    password = "test-pass-123"

    print(f"\nQForm 冒烟测试  →  {BASE}\n")
    client = Client()

    # ---------------------------------------------------------- 账号
    print("1. 账号")
    status, html, _, _ = client.get("/register")
    check("注册页可访问", status == 200, f"status={status}")

    # 静态资源要带内容哈希，否则更新部署后浏览器和 CDN 还在用旧的 css
    _, landing, _, _ = client.get("/")
    check("静态资源地址带版本号（更新后不会用到旧缓存）",
          "app.css?v=" in landing and "app.js?v=" in landing)

    check("登录 / 注册的密码框都有「小眼睛」",
          html.count("data-pw-toggle") == 2,        # 注册页：密码 + 确认密码
          f"注册页找到 {html.count('data-pw-toggle')} 个")

    client.load_csrf("/register")
    check("注册页带 CSRF token", bool(client.csrf))

    status, html, url, _ = client.post_form("/register", {
        "username": user, "password": password, "confirm": password,
    })
    check("注册成功并进入任务页", status == 200 and url.endswith("/"),
          f"status={status} url={url}")
    check("导航里显示的是用户名", user in html)

    status, _, _, _ = client.get("/register")
    check("已登录再访问注册页会跳转", status == 200)

    # 未登录访问受保护页面
    anon = Client()
    status, _, url, _ = anon.get("/tasks/whatever")
    check("未登录访问任务页会跳到登录", "/login" in url, f"url={url}")

    # ---------------------------------------------------------- 任务
    print("\n2. 创建任务")
    client.load_csrf("/")
    status, html, url, _ = client.post_form("/tasks", {
        "name": "初二(3)班 课前小调查", "description": "冒烟测试用",
    })
    apiid = url.rstrip("/").rsplit("/", 1)[-1] if "/tasks/" in url else ""
    check("任务创建成功", status == 200 and bool(apiid), f"status={status} url={url}")
    check("任务编号长度是 12", len(apiid) == 12, f"apiid={apiid}")
    if not apiid:
        print("\n无法继续，退出。")
        return 1
    print(f"       接口地址 {BASE}/api/{apiid}")
    check("概览页展示了接口地址", f"/api/{apiid}" in html)

    # ---------------------------------------------------------- 数据接口
    print("\n3. 数据接口：写入与读取")
    rows = [
        {"姓名": "张三", "掌握程度": "完全掌握", "用时": 92},
        {"姓名": "李四", "掌握程度": "基本掌握", "用时": 78},
        {"姓名": "王五", "掌握程度": "还没听懂", "用时": 55},
        {"姓名": "赵六", "掌握程度": "完全掌握", "额外字段": "临时加的"},
        {"姓名": "钱七", "tags": ["a", "b"], "flag": True},
    ]
    for i, row in enumerate(rows, 1):
        body = json.dumps(row, ensure_ascii=False).encode("utf-8")
        status, raw, _, headers = client.raw(
            "POST", f"/api/{apiid}", body=body,
            headers={"Content-Type": "application/json"})
        payload = json.loads(raw)
        check(f"第 {i} 条提交成功",
              status == 200 and payload.get("ok") and payload.get("id"),
              f"status={status} body={raw[:100]}")
        check(f"第 {i} 条返回 CORS 头",
              headers.get("Access-Control-Allow-Origin") == "*")

    # 表单编码
    status, raw, _, _ = client.raw(
        "POST", f"/api/{apiid}",
        body=urllib.parse.urlencode({"姓名": "表单同学"}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    check("表单编码也能提交", status == 200 and json.loads(raw)["ok"],
          f"status={status}")

    status, raw, _, _ = client.get(f"/api/{apiid}")
    payload = json.loads(raw)
    check("GET 只返回最近若干条", len(payload["submissions"]) == 3,
          f"got {len(payload['submissions'])}")
    check("total 是 6", payload["total"] == 6, f"got {payload['total']}")
    check("带 task 信息", payload["task"]["id"] == apiid)
    check("带说明字段告诉你去哪拿全部", "/all" in payload.get("note", ""))
    check("每条记录带 _id 和 _at",
          all("_id" in s and "_at" in s for s in payload["submissions"]))

    status, raw, _, _ = client.get(f"/api/{apiid}/all")
    payload = json.loads(raw)
    check("全量接口返回 6 条", payload["total"] == 6)
    check("自定义字段原样保留",
          any(s.get("额外字段") == "临时加的" for s in payload["submissions"]))
    check("数组字段原样保留",
          any(s.get("tags") == ["a", "b"] for s in payload["submissions"]))

    # 突发读取要放行：一个班的学生走学校同一个出口 IP，
    # 如果按"最小间隔"限流，第二个人打开看板就被拒了。
    burst_ok = 0
    for _ in range(10):
        status, _, _, _ = client.get(f"/api/{apiid}/all")
        if status == 200:
            burst_ok += 1
        else:
            break
    check("连续 10 次全量读取全部放行（全班同时刷新看板）", burst_ok == 10,
          f"只成功 {burst_ok} 次")

    # 但并发猛刷还是会被拦住。
    # 注意要用并发：顺序请求受网络延迟限制，消耗速度追不上令牌补充速度，
    # 在慢网络下永远刷不爆 —— 而真正要防的正是"页面里写死循环并发刷新"。
    def hammer(_index):
        req = urllib.request.Request(
            BASE + f"/api/{apiid}/all",
            headers={"User-Agent": "QiuForm-SmokeTest/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, ""
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:                      # noqa: BLE001
            return 0, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(hammer, range(40)))
    codes = [code for code, _ in results]
    check("并发猛刷会被限流（429）", 429 in codes,
          f"40 个并发请求，返回码：{sorted(set(codes))}")
    limited_body = next((body for code, body in results if code == 429), "")
    if limited_body:
        check("429 里带上 retry_after",
              "retry_after" in json.loads(limited_body), limited_body[:120])

    status, raw, _, _ = client.get("/api/nonexistent00")
    check("不存在的任务返回 404", status == 404, f"status={status}")

    # ---------------------------------------------------------- 多模态
    print("\n4. 多模态：上传文件")
    png = make_png()
    status, raw, _, _ = client.post(
        f"/api/{apiid}", {"category": "橡皮"},
        files=[("file", "文具.png", "image/png", png)])
    payload = json.loads(raw)
    check("multipart 提交成功", status == 200 and payload.get("ok"),
          f"status={status} {raw[:120]}")
    urls = payload.get("attachments") or []
    check("返回附件路径", len(urls) == 1 and urls[0].startswith("/files/"),
          f"{urls}")

    # 中文字段名曾经被 latin-1 解成乱码，这里钉住
    status, raw, _, _ = client.post(
        f"/api/{apiid}", {"姓名": "林小满", "跟上程度": "完全跟上"},
        files=[("图片", "作业.png", "image/png", png)])
    check("中文字段名的 multipart 提交成功", status == 200, f"status={status}")
    status, raw, _, _ = client.get(f"/api/{apiid}?limit=1")
    last = (json.loads(raw).get("submissions") or [{}])[0]
    check("中文字段名原样入库（不是乱码）",
          last.get("姓名") == "林小满" and last.get("跟上程度") == "完全跟上",
          f"keys={list(last.keys())}")

    if urls:
        status, blob, _, headers = client.raw("GET", urls[0])
        check("附件可公开访问", status == 200, f"status={status}")
        check("Content-Type 正确",
              (headers.get("Content-Type") or "").startswith("image/png"),
              str(headers.get("Content-Type")))
        check("取回字节与上传完全一致", blob == png,
              f"{len(blob)} vs {len(png)}")

    status, raw, _, _ = client.post(
        f"/api/{apiid}", {}, files=[("file", "x.exe",
                                     "application/octet-stream", b"MZ\x00")])
    check("不支持的扩展名被拒绝（415）", status == 415, f"status={status}")

    time.sleep(1.0)

    # ---------------------------------------------------------- 页面托管
    print("\n5. 上传任务页面（HTML 托管）")
    page_html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>课堂反馈</title></head>
<body>
<h1>课堂反馈</h1>
<form id="f"><input name="name"><button>提交</button></form>
<script>
fetch("http://127.0.0.1:9999/api/OLDOLDOLDOLD", {method: "POST"});
</script>
</body></html>"""
    client.load_csrf(f"/tasks/{apiid}/pages")
    status, html, url, _ = client.post(
        f"/tasks/{apiid}/pages/upload",
        {"csrf_token": client.csrf, "make_primary": "1"},
        files=[("files", "index.html", "text/html", page_html.encode("utf-8"))])
    check("上传 HTML 成功", status == 200, f"status={status}")
    check("提示里说明了地址被改写", "改写" in html, html[:200])
    check("文件出现在页面列表里", "index.html" in html)

    status, served, _, headers = client.raw("GET", f"/p/{apiid}/index.html")
    text = served.decode("utf-8", "replace")
    check("上传的页面可以公开访问", status == 200, f"status={status}")
    check("页面内容原样返回", "课堂反馈" in text)
    check("旧的接口地址被自动改写",
          f"/api/{apiid}" in text and "OLDOLDOLDOLD" not in text,
          text[:200])
    check("主页根路径会跳到页面",
          client.raw("GET", f"/p/{apiid}/")[0] == 200)

    # 配套资源
    client.load_csrf(f"/tasks/{apiid}/pages")
    status, html, _, _ = client.post(
        f"/tasks/{apiid}/pages/upload",
        {"csrf_token": client.csrf},
        files=[("files", "style.css", "text/css", b"body{color:red}")])
    check("配套资源可以一起上传", status == 200)
    status, css, _, _ = client.raw("GET", f"/p/{apiid}/style.css")
    check("配套资源能访问到", status == 200 and b"color:red" in css,
          f"status={status}")

    status, _, _, _ = client.raw("GET", f"/p/{apiid}/../qiuform/db.py")
    check("路径穿越被挡住", status in (400, 404), f"status={status}")

    status, svg, _, _ = client.raw("GET", f"/tasks/{apiid}/qr.svg")
    check("二维码接口返回 SVG",
          status == 200 and svg.startswith(b"<svg"), f"status={status}")

    # ---------------------------------------------------------- 数据页
    print("\n6. 后台数据页与导出")
    status, html, _, _ = client.get(f"/tasks/{apiid}/data")
    check("数据页可访问", status == 200)
    check("数据页列出了动态字段", "掌握程度" in html)
    check("附件显示成图片缩略图", "thumb" in html)

    status, csv, _, headers = client.raw("GET", f"/tasks/{apiid}/data.csv")
    csv_text = csv.decode("utf-8-sig", "replace")
    check("CSV 导出成功", status == 200, f"status={status}")
    check("CSV 表头含动态字段", "额外字段" in csv_text)
    check("CSV 带 BOM（Excel 不乱码）", csv.startswith(b"\xef\xbb\xbf"))

    status, blob, _, _ = client.raw("GET", f"/tasks/{apiid}/data.json")
    exported = json.loads(blob)
    check("JSON 导出成功", status == 200 and exported["total"] == 8,
          f"status={status} total={exported.get('total')}")

    # ---------------------------------------------------------- 读写模式
    print("\n7. 读写模式")
    client.load_csrf(f"/tasks/{apiid}/settings")
    client.post_form(f"/tasks/{apiid}/settings", {"action": "mode",
                                                  "mode": "write_only"})
    status, raw, _, _ = client.get(f"/api/{apiid}/all")
    check("切到「只写」后读取被拒绝（403）", status == 403, f"status={status}")
    status, raw, _, _ = client.raw(
        "POST", f"/api/{apiid}", body=b'{"x":1}',
        headers={"Content-Type": "application/json"})
    check("切到「只写」后仍可提交", status == 200, f"status={status}")

    client.load_csrf(f"/tasks/{apiid}/settings")
    client.post_form(f"/tasks/{apiid}/settings", {"action": "mode",
                                                  "mode": "read_only"})
    status, raw, _, _ = client.raw(
        "POST", f"/api/{apiid}", body=b'{"x":1}',
        headers={"Content-Type": "application/json"})
    check("切到「只读」后提交被拒绝（403）", status == 403, f"status={status}")

    client.post_form(f"/tasks/{apiid}/settings", {"action": "mode",
                                                  "mode": "read_write"})

    # ---------------------------------------------------------- 安全
    print("\n8. 安全与权限")
    status, raw, _, _ = client.raw(
        "POST", f"/api/{apiid}", body=b'{"x":1}',
        headers={"Content-Type": "application/json"})
    check("数据接口不需要 CSRF（外部网页才能直接调）", status == 200,
          f"status={status}")

    status, raw, _, _ = client.post(f"/tasks/{apiid}/settings",
                                    {"action": "mode", "mode": "closed"})
    check("网页表单缺 CSRF 被拒绝（400）", status == 400, f"status={status}")

    other = Client()
    other.load_csrf("/register")
    other.post_form("/register", {"username": f"other{stamp}",
                                  "password": password, "confirm": password})
    other.load_csrf("/")   # 登录会重置会话，CSRF 要重新取
    status, _, url, _ = other.get(f"/tasks/{apiid}")
    check("别人的任务访问不到（404）", status == 404, f"status={status}")
    status, _, _, _ = other.raw("POST", f"/tasks/{apiid}/delete",
                                body=urllib.parse.urlencode(
                                    {"csrf_token": other.csrf,
                                     "confirm": "初二(3)班 课前小调查"}).encode())
    check("别人删不掉我的任务", status == 404, f"status={status}")

    # ---------------------------------------------------------- 清理
    print("\n9. 删除任务")
    client.load_csrf(f"/tasks/{apiid}/settings")
    status, html, url, _ = client.post_form(
        f"/tasks/{apiid}/delete", {"confirm": "名字故意写错"})
    check("任务名对不上不删除", f"/tasks/{apiid}" in url, f"url={url}")

    status, html, url, _ = client.post_form(
        f"/tasks/{apiid}/delete", {"confirm": "初二(3)班 课前小调查"})
    check("名字对上后删除成功并回到首页",
          url.rstrip("/") == BASE.rstrip("/"), f"url={url}")
    status, _, _, _ = client.get(f"/api/{apiid}")
    check("删除后接口返回 404", status == 404, f"status={status}")
    status, _, _, _ = client.raw("GET", f"/p/{apiid}/index.html")
    check("删除后页面也不再对外服务", status == 404, f"status={status}")

    print("\n10. 退出登录")
    client.load_csrf("/")
    status, html, url, _ = client.post_form("/logout", {})
    check("退出后回到首页（不是停在登录页）",
          url.rstrip("/") == BASE.rstrip("/"), f"url={url}")
    check("首页给出登录和注册入口", "登录" in html and "注册" in html)

    print(f"\n结果：{passed} 项通过，{failed} 项失败\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
