# 部署说明

线上地址：<https://qiuform.qiuzizhao.com>

## 线上环境长什么样

| | |
| --- | --- |
| 服务器 | 腾讯云，Ubuntu 24.04 LTS，x86_64 |
| 代码目录 | `/opt/qiuform` |
| 数据目录 | `/var/lib/qiuform`（数据库、上传的网页和附件、会话密钥） |
| 服务用户 | `qiuform`（系统用户，不能登录） |
| 监听 | `127.0.0.1:8791`，只对本机开放 |
| 进程管理 | systemd 服务 `qiuform` |
| 反向代理 | nginx，站点配置 `/etc/nginx/sites-available/qiuform` |
| 域名 | `qiuform.qiuzizhao.com`，前面挂了 Cloudflare |

> 这台服务器上还跑着十几个别的站点（schedule、timer、clipboard…）。
> 加站点时**只新增配置、不动已有的**，改完先 `nginx -t` 再 reload。

## 从零部署一台新机器

```bash
# 1. 上传代码（在本地执行）
tar -czf qiuform-src.tar.gz \
    --exclude='__pycache__' --exclude='.vendor' --exclude='data' \
    run.py requirements.txt qiuform deploy examples tests .gitignore
scp -P <ssh端口> qiuform-src.tar.gz <用户>@<服务器>:/tmp/

# 2. 解压并安装（在服务器上执行）
sudo mkdir -p /opt/qiuform
sudo tar -xzf /tmp/qiuform-src.tar.gz -C /opt/qiuform
sudo bash /opt/qiuform/deploy/setup.sh        # 建用户、装依赖、起服务
sudo bash /opt/qiuform/deploy/enable-nginx.sh # 配 nginx（会先 nginx -t，失败自动回滚）
```

想改端口、域名、数据目录，用环境变量覆盖：

```bash
sudo QIUFORM_PORT=9000 \
     QIUFORM_BASE_URL=https://another.example.com \
     QIUFORM_DATA_DIR=/data/qiuform \
     bash /opt/qiuform/deploy/setup.sh
```

## 更新代码

```bash
scp -P <ssh端口> qiuform-src.tar.gz <用户>@<服务器>:/tmp/
ssh -p <ssh端口> <用户>@<服务器>
sudo tar -xzf /tmp/qiuform-src.tar.gz -C /opt/qiuform
sudo bash /opt/qiuform/deploy/setup.sh
```

`setup.sh` 可以反复执行。它会重新装依赖并**重启服务**，
所以改完代码一定要跑一次，光覆盖文件是不会生效的。

## 常用命令

```bash
systemctl status qiuform            # 服务状态
journalctl -u qiuform -f            # 实时日志
journalctl -u qiuform -n 100        # 最近 100 行
systemctl restart qiuform           # 重启

nginx -t                            # 校验 nginx 配置
systemctl reload nginx              # 重载
```

## 备份

所有数据都在一个目录里，打包即备份：

```bash
sudo tar -czf ~/qiuform-backup-$(date +%F).tar.gz -C /var/lib qiuform
```

恢复就是解回去，注意带上权限：
`sudo tar -xzf 备份.tar.gz -C /var/lib && sudo chown -R qiuform:qiuform /var/lib/qiuform`

## 几个容易踩的坑

**生成了 `http://127.0.0.1` 的地址。**
站点跑在反向代理后面，WSGI 看到的是内网地址。服务文件里已经设了
`QIUFORM_TRUST_PROXY=1` 和 `QIUFORM_BASE_URL`，两个都保留着最稳：
前者还原真实客户端 IP（限流要用），后者钉死对外域名。

**改了代码但行为没变。**
多半是服务没重启。`setup.sh` 用的是 `systemctl restart`，
手动部署的话记得自己也 restart 一次。

**上传大文件被拦。**
nginx 的 `client_max_body_size`（现在 64m）和应用里的
`MAX_CONTENT_LENGTH`（48m）要匹配，两个都别超过对方太多。

**读取限流怎么调。**
读取限流按「任务 + 客户端 IP」计，允许突发 20 次、之后每秒补 5 次
（`QIUFORM_READ_BURST` / `QIUFORM_READ_RATE`）。之所以给这么宽的突发，
是因为一个班的学生走学校同一个出口 IP，如果按"最小间隔"限流，
第二个人打开看板就会被拒。要在 systemd 里调整：

```bash
sudo systemctl edit qiuform     # 加 [Service] Environment=QIUFORM_READ_BURST=50
sudo systemctl restart qiuform
```

**Cloudflare 拦了脚本请求。**
默认的 `Python-urllib/x.y` 这个 User-Agent 会被 Cloudflare 的机器人规则
返回 403；`curl`、浏览器、`python-requests` 都正常。写脚本调接口时
带一个正常的 User-Agent 就行。

**不想再用这个站点了。**
```bash
sudo rm -f /etc/nginx/sites-enabled/qiuform /etc/nginx/sites-available/qiuform
sudo systemctl reload nginx
sudo systemctl disable --now qiuform
```
数据还在 `/var/lib/qiuform`，确认不要了再删。
