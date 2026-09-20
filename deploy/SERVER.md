# Linux 服务器部署速查（/root/qq-cat-bot）

> 首次部署完成于 2026-09-21。本文件是运维速查，不含任何密钥。

## 一、基本盘

| 项 | 值 |
|---|---|
| 服务器 | 阿里云 ECS `i-j6ce8sjdun47da4yi9y0`，**cn-hongkong**，2 核 2G |
| 公网 IP | `47.83.166.37` |
| 系统 | Alibaba Cloud Linux 4（anolis 系，包管理器 **dnf**） |
| 项目路径 | `/root/qq-cat-bot` |
| 解释器 | `/root/qq-cat-bot/.venv/bin/python`（Python 3.11.6） |
| systemd 服务 | **`qqbot`**（`enabled` + `Restart=always`） |
| 登入方式 | **SSH 公钥**（密码登录已禁用）。密钥 `tmp/keys/qqbot_ecs` |

## 二、日常运维

```bash
systemctl status qqbot            # 看状态
systemctl restart qqbot           # 重启
systemctl stop qqbot              # 停止
journalctl -u qqbot -f            # 实时日志（systemd 视角）

tail -f /root/qq-cat-bot/logs/bot.log       # 主日志
tail -f /root/qq-cat-bot/logs/bot.err.log   # botpy 日志
tail -f /root/qq-cat-bot/cloudflared.log    # 隧道日志

bash deploy/start.sh --restart    # 不走 systemd 时的重启方式
```

## 三、端口 / 域名

| 端口 | 用途 | 公网域名 |
|---|---|---|
| 9090 | WebUI 后台 | 仅本机（如需外网自行开隧道路由） |
| 9091 | Webhook 回调 | `https://qqbot.deemo8848.dpdns.org` |
| 9092 | 静态页（`bot/public_html/`） | `https://page.deemo8848.dpdns.org` |
| 8082/8083 | 预留 proj1/proj2 | `proj1/proj2.deemo8848.dpdns.org` |

## 四、★ 装依赖的坑（务必按此装）

### 1. bilibili 一定要装 `bilibili-api-python`
```bash
.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  "bilibili-api-python==17.2.0" \
  pycryptodomex pyjwt qrcode qrcode-terminal requests websockets markdownify
```
> 它的 wheel `requires_dist` 为空 → **依赖必须手工补**。
> **不要**装 `bilibili-api`（9.1.0 是废弃旧 fork，缺 `login_v2`/`opus`）。

### 2. pip 必须用清华源
```bash
-i https://pypi.tuna.tsinghua.edu.cn/simple
```
阿里云源在这台机上 index 响应要 11 秒，慢到几乎挂住。

### 3. skia 需要系统图形库
```bash
dnf install -y mesa-libEGL mesa-libGL fontconfig
```
（不装会 `ImportError: libEGL.so.1`）

### 4. meme-generator 素材要单独拉
```bash
cd /root/_memesrc
curl -fL -o v0.1.14.tar.gz \
  https://github.com/MemeCrafters/meme-generator/archive/refs/tags/v0.1.14.tar.gz
tar xzf v0.1.14.tar.gz -C src
SP=/root/qq-cat-bot/.venv/lib/python3.11/site-packages
mv $SP/meme_generator/memes $SP/meme_generator/memes.pipbak   # 备份
cp -r src/meme-generator-0.1.14/meme_generator/memes $SP/meme_generator/memes
cp -rn src/meme-generator-0.1.14/resources/. $SP/meme_generator/resources/
.venv/bin/python bot/meme/rebuild_data.py       # 重建关键词
```
> pip wheel 里的 `memes/` 有 282 个模板但 **0 张图**；覆盖后有 240 个带图。

### 5. Pillow 版本三方打架（现状可用）
- `meme-generator 0.1.14` 要 `<11`、`apilmoji` 要 `>=11`、`pil-utils` 要 `<12`
- 实测 **12.3.0 下三者都能 import 且出图正常**
- **若 meme 出图异常，第一个怀疑 Pillow 版本**，退到 11.x

## 五、隧道（cloudflared）

配置文件 `/root/qq-cat-bot/tunnel/config.yml`（**不在仓库里**，需手工维护）：
```yaml
tunnel: a87b43d7-88a8-4ac6-89d4-c680313bf2a8
credentials-file: /root/.cloudflared/a87b43d7-88a8-4ac6-89d4-c680313bf2a8.json
protocol: http2
ingress: ...
```
凭据 JSON 权限需 `600`，目录 `700`。

**验证是否真连上**（bot 控制台打印"已启动"不算数）：
```bash
grep "Registered tunnel connection" cloudflared.log | tail -4
```

## 六、改代码后如何生效

```bash
cd /root/qq-cat-bot
git pull --ff-only
[ requirements.txt 变了 ] && .venv/bin/python -m pip install -r requirements.txt
systemctl restart qqbot
```
或在 QQ 里给 bot 发 **`bot更新`**（管理员），Linux 下用 `os.execv` 原地重启，systemd 无感。

## 七、资源目录

| 路径 | 内容 | 来源 |
|---|---|---|
| `resources/image_lib/dragon/` | 龙图 189 张 | 本机打包上传（不在仓库） |
| `bot/meme/custom_memes/feiyu/` | 自研 meme 插件 | 同上（仓库里已带） |
| `tools/{BBDown,ffmpeg,cloudflared}` | 外部二进制 | install.sh 下载 / 复制自 `/usr/local/bin` |
