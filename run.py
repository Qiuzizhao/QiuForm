#!/usr/bin/env python3
"""QiuForm 启动入口。

    python run.py                 # 默认 8000 端口
    python run.py --port 9000
    python run.py --host 0.0.0.0 --port 8000

依赖可以用两种方式提供：
  1. 正常安装：  pip install -r requirements.txt
  2. 项目内目录：把包装进 .vendor/（本仓库就是这么跑的）
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 允许把依赖放在项目内的 .vendor 目录里
VENDOR = ROOT / ".vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

try:
    from qiuform import create_app
    from qiuform.utils import local_ip
except ImportError as exc:  # pragma: no cover
    print(f"缺少依赖：{exc}")
    print("请先执行：pip install -r requirements.txt")
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 QiuForm")
    parser.add_argument("--host", default=os.environ.get("QIUFORM_HOST", "0.0.0.0"),
                        help="监听地址，默认 0.0.0.0（可用 QIUFORM_HOST 指定）")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("QIUFORM_PORT", "8000")),
                        help="监听端口，默认 8000（可用 QIUFORM_PORT 指定）")
    parser.add_argument("--debug", action="store_true", help="开启调试模式（自动重载）")
    parser.add_argument("--dev", action="store_true", help="用 Flask 自带服务器，不用 waitress")
    args = parser.parse_args()

    app = create_app()
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host

    print()
    print("  QiuForm 已启动")
    print(f"    本机访问    http://{shown_host}:{args.port}/")
    if args.host in ("0.0.0.0", "::"):
        print(f"    局域网访问  http://{local_ip()}:{args.port}/")
    print(f"    数据目录    {app.config['DATA_DIR']}")
    if app.config.get("BASE_URL"):
        print(f"    对外地址    {app.config['BASE_URL']}")
    if app.config.get("TRUST_PROXY"):
        print("    反向代理    已开启（信任 X-Forwarded-*）")
    print("    按 Ctrl+C 停止")
    print()

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
        return 0

    if not args.dev:
        try:
            from waitress import serve
            serve(app, host=args.host, port=args.port, threads=8,
                  ident="QiuForm")
            return 0
        except ImportError:
            print("  提示：未安装 waitress，改用 Flask 自带服务器")

    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
