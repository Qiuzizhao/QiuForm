#!/usr/bin/env bash
#
# QForm 服务器安装脚本（在服务器上以 root 运行）
#
#   bash deploy/setup.sh
#
# 会做这些事：
#   1. 建一个专用系统用户 qiuform
#   2. 建数据目录（数据库、上传的网页和附件）
#   3. 建 Python 虚拟环境并装依赖
#   4. 安装并启动 systemd 服务
#   5. 本机自检一下有没有起来
#
# 可以重复执行：改动代码后再跑一次就等于重新部署。
#
# 想改端口 / 域名 / 数据目录，用环境变量：
#   QIUFORM_PORT=9000 QIUFORM_BASE_URL=https://x.example.com bash deploy/setup.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${QIUFORM_DATA_DIR:-/var/lib/qiuform}"
SERVICE_USER="${QIUFORM_USER:-qiuform}"
PORT="${QIUFORM_PORT:-8791}"
BASE_URL="${QIUFORM_BASE_URL:-https://qiuform.qiuzizhao.com}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
UNIT_PATH=/etc/systemd/system/qiuform.service

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m警告：\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m错误：\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行（sudo bash deploy/setup.sh）"

say "检查环境"
command -v "$PYTHON_BIN" >/dev/null || die "找不到 $PYTHON_BIN，先装 Python 3"
"$PYTHON_BIN" - <<'PY' || die "Python 版本太低，需要 3.8 以上"
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
PY
"$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || \
  die "缺少 venv 模块，Debian/Ubuntu 上执行：apt install -y python3-venv"
command -v systemctl >/dev/null || die "这台机器没有 systemd，无法用本脚本部署"
echo "    Python：$("$PYTHON_BIN" --version)"
echo "    代码目录：$APP_DIR"
echo "    数据目录：$DATA_DIR"
echo "    监听端口：127.0.0.1:$PORT"

say "创建服务用户 $SERVICE_USER"
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "    已存在，跳过"
else
  NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"
  useradd --system --no-create-home --shell "$NOLOGIN" "$SERVICE_USER"
  echo "    已创建"
fi

say "创建数据目录"
mkdir -p "$DATA_DIR"
echo "    $DATA_DIR"

say "安装 Python 依赖"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
echo "    已装：$("$APP_DIR/.venv/bin/python" -m pip list --format=freeze \
  | grep -iE '^(Flask|waitress)=' | paste -sd' ' -)"

say "设置文件权限"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR" "$DATA_DIR"
chmod -R u+rwX,go-rwx "$DATA_DIR"
echo "    数据目录仅 $SERVICE_USER 可读写"

say "安装 systemd 服务"
sed -e "s|__APP_DIR__|$APP_DIR|g" \
    -e "s|__DATA_DIR__|$DATA_DIR|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|https://qiuform.qiuzizhao.com|$BASE_URL|g" \
    "$APP_DIR/deploy/qiuform.service" > "$UNIT_PATH"
systemctl daemon-reload
# 注意：不能用 enable --now —— 服务已经在跑时它是空操作，
# 重新部署就变成"文件更新了但进程还是老的"。
systemctl enable qiuform
systemctl restart qiuform
sleep 2
systemctl --no-pager --lines=0 status qiuform || true

say "本机自检"
if curl -fsS -m 5 -o /dev/null "http://127.0.0.1:$PORT/login"; then
  echo "    服务已响应 http://127.0.0.1:$PORT"
else
  warn "服务没有响应，看看日志：journalctl -u qiuform -n 50 --no-pager"
  exit 1
fi

cat <<EOF

安装完成。

接下来把 nginx 配好（这一步单独做，避免影响服务器上已有的站点）：

  1. 看看 80 端口有没有被占用：
       ss -lntp | grep ':80 '

  2. 复制站点配置并检查：
       cp $APP_DIR/deploy/nginx-qiuform.conf /etc/nginx/conf.d/qiuform.conf
       nginx -t

  3. 检查通过再重载（这一步会影响线上，务必先看 nginx -t 的结果）：
       systemctl reload nginx

然后用浏览器打开 $BASE_URL 试试。

常用命令：
  systemctl status qiuform          看服务状态
  journalctl -u qiuform -f          实时看日志
  systemctl restart qiuform         重启
  systemctl stop qiuform            停止

数据都在 $DATA_DIR，备份直接打包这个目录：
  tar -czf qiuform-backup-\$(date +%F).tar.gz -C $(dirname "$DATA_DIR") $(basename "$DATA_DIR")
EOF
