#!/usr/bin/env bash
#
# 启用 QiuForm 的 nginx 站点（在服务器上以 root 运行）
#
#   sudo bash deploy/enable-nginx.sh
#
# 只新增一个 server 块，不碰服务器上已有的任何站点。
# 会先跑 nginx -t 校验，不通过就自动撤掉，不会把 nginx 弄挂。

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AVAIL=/etc/nginx/sites-available/qiuform
ENABLED=/etc/nginx/sites-enabled/qiuform
SOURCE="$APP_DIR/deploy/nginx-qiuform.conf"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m错误：\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行（sudo bash deploy/enable-nginx.sh）"
command -v nginx >/dev/null || die "这台机器没装 nginx"
[[ -f "$SOURCE" ]] || die "找不到 $SOURCE"

say "复制站点配置"
cp "$SOURCE" "$AVAIL"
ln -sfn "$AVAIL" "$ENABLED"
echo "    $AVAIL"
echo "    $ENABLED -> $AVAIL"

say "校验 nginx 配置"
if nginx -t; then
  say "重载 nginx"
  systemctl reload nginx
  echo "    已重载，配置生效"
else
  say "配置有误，回滚"
  rm -f "$ENABLED" "$AVAIL"
  nginx -t || true
  die "已撤掉刚加的配置，nginx 保持原样。请检查 $SOURCE"
fi

say "本机验证"
if curl -fsS -m 8 -o /dev/null -H 'Host: qiuform.qiuzizhao.com' http://127.0.0.1/login; then
  echo "    nginx 已能把 qiuform.qiuzizhao.com 转发到后端"
else
  die "转发没成功，检查 systemctl status qiuform 和 nginx 错误日志"
fi

echo
echo "完成。用浏览器打开 https://qiuform.qiuzizhao.com 看看。"
echo "回滚（万一要撤掉这个站点）：rm -f $ENABLED $AVAIL && systemctl reload nginx"
