#!/usr/bin/env python3
"""校验 examples/ 里示例网页的内联 JavaScript 语法。

示例是给人直接拿去用的，语法错一个字符就整个页面白屏。
用 node --check 过一遍，没装 node 就跳过。

    python tests/check_examples.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)

# 浏览器的 window 上有一批同名属性。顶层用 var 声明同名变量时，
# 声明会写到 window 上，而其中有些属性是"只能存字符串"的：
#   var status = document.getElementById('status')
# 元素会被强行转成字符串，之后 status.textContent = ... 全部静默失效，
# 页面看不出报错，只是什么都不发生。这个坑踩过两次了，这里静态拦一下。
WINDOW_NAMES = [
    "status", "name", "origin", "title",
    "top", "parent", "self", "location", "history", "event", "external",
    "length", "closed", "opener", "screen", "frames", "navigator", "document",
]
DECL_RE = re.compile(
    # [^;]*? 而不是 [^;=]*?：同一条 var 语句里可能先有别的声明
    # （var a = x, status = y 这种），要能跨过前面的等号
    r"\b(?:var|let|const)\s+[^;]*?\b(" + "|".join(WINDOW_NAMES) + r")\s*="
)


def window_collisions(code):
    """找出疑似顶层、且与 window 内置属性同名的声明。"""
    found = []
    for match in DECL_RE.finditer(mask_js_comments(code)):
        name = match.group(1)
        line_start = code.rfind("\n", 0, match.start()) + 1
        line_end = code.find("\n", match.start())
        line = code[line_start:line_end if line_end != -1 else len(code)]
        indent = len(line) - len(line.lstrip(" "))
        line_no = code.count("\n", 0, match.start()) + 1
        # 缩进 <= 2 空格视为脚本顶层；函数体内的 var 是函数作用域，不受影响
        if indent <= 2:
            found.append((line_no, name, line.strip()))
    return found


def mask_js_comments(code: str) -> str:
    """把注释内容替换成空格，长度和换行都保持原样。

    这样行号不会错位，也不会把注释里举例的代码当成真的声明。
    字符串里的 // 和 /* 不会误伤。
    """
    chars = list(code)
    i, n = 0, len(code)
    quote = None
    while i < n:
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "/":
            end = code.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                chars[k] = " "
            i = end
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "*":
            end = code.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                if chars[k] != "\n":
                    chars[k] = " "
            i = end
            continue
        i += 1
    return "".join(chars)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("跳过：没有安装 node")
        return 0

    files = sorted(EXAMPLES.glob("*.html"))
    if not files:
        print("examples/ 里没有 html 文件")
        return 1

    failed = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        blocks = SCRIPT_RE.findall(text)
        if not blocks:
            print(f"  [跳过] {path.name}（没有脚本）")
            continue

        problems = []
        for index, code in enumerate(blocks, 1):
            if "src=" in code[:80]:
                continue

            for line_no, name, line in window_collisions(code):
                problems.append(
                    f"第 {line_no} 行顶层声明了 `{name}`，与 window.{name} 重名，"
                    f"赋值会静默失效：{line}"
                )

            with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(code)
                tmp_path = tmp.name
            result = subprocess.run(
                [node, "--check", tmp_path],
                capture_output=True, text=True,
            )
            Path(tmp_path).unlink(missing_ok=True)
            if result.returncode != 0:
                message = (result.stderr or "").strip().splitlines()
                problems.append(f"第 {index} 段：{message[-1] if message else '语法错误'}")

        if problems:
            failed += 1
            print(f"  [失败] {path.name}")
            for line in problems:
                print(f"         {line}")
        else:
            print(f"  [通过] {path.name}（{len(blocks)} 段脚本）")

    print(f"\n共 {len(files)} 个示例，{failed} 个有问题")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
