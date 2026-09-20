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
| `data/` | **用户数据**（钱包/卡牌/钓鱼/运势/卡牌素材） | 本机打包上传（**不在仓库**，见第八节） |

## 八、★ 用户数据 `data/` 迁移

`data/` 被 `.gitignore` 排除，**`git pull` 永远不会带来用户数据**。新机器首次部署后必须手工迁移，否则表现为「用户数据全空 / 发消息后进度变回初始状态」。

### 存放内容
| 路径 | 内容 |
|---|---|
| `data/wallet.json` | 共享钱包（喵喵币）余额 |
| `data/cards/data.json` | 卡牌图鉴 / 归属 / 在售 |
| `data/cards/key_secret.txt` | 卡牌密钥 |
| `data/cards/assets/<hash>/` | 卡牌素材（back/card/front/foreground.png、card.html），**体积大头** |
| `data/fishing/fishing_data.json` | 钓鱼进度、库存、图鉴、成就 |
| `data/jrys/jrys_data.json` | 今日运势签到记录 |

### 迁移步骤（本机 → 服务器）
```bash
# 1. 本机打包（排除 *.bak_* / __pycache__）
python tmp/pack_data.py          # 产出 tmp/data_pack.tar.gz

# 2. 服务器上先备份现有数据（防覆盖）
cd /root/qq-cat-bot && tar czf data.before-import-$(date +%Y%m%d_%H%M%S).tar.gz data

# 3. 上传 + 解包
python tmp/ecsrun.py --put <本机>/tmp/data_pack.tar.gz /root/qq-cat-bot/data_pack.tar.gz
# 服务器上：tar xzf data_pack.tar.gz -C /root/qq-cat-bot

# 4. 校验 md5 一致（关键！）
#    本机与服务器分别 md5sum，4 个 JSON 必须逐个相同
```

### ★ 是否需要重启？
**不需要。** `wallet.py` / `carddata.py` / `fishing/core.py` / `jrys.py` 四个模块都是**每次操作实时 `_load()` 读盘**，无内存缓存，覆盖文件后立即生效。
（但覆盖后仍建议 `systemctl restart qqbot` 一次，确保 WebUI 后台页面的统计也刷新。）

### 反向：服务器数据取回本机
```bash
python tmp/ecsrun.py --get /root/qq-cat-bot/data/wallet.json <本机>/tmp/wallet.json
```
生产环境若两边都在跑，**不要互相覆盖** —— 应停机后单向同步，否则会丢数据。

## 九、systemd 服务文件参考

```ini
[Unit]
Description=QQ Cat Bot (qq-botpy)
After=network-online.target
Wants=network-online.target
# 崩溃频繁时不要无脑重启，10 分钟内最多 5 次
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=/root/qq-cat-bot
ExecStart=/root/qq-cat-bot/.venv/bin/python /root/qq-cat-bot/main.py
Restart=always
RestartSec=10
StandardOutput=append:/root/qq-cat-bot/logs/bot.log
StandardError=append:/root/qq-cat-bot/logs/bot.err.log
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```
> ⚠️ `StartLimitIntervalSec` / `StartLimitBurst` **必须放 `[Unit]` 段**。放 `[Service]` 里 systemd 会打印
> `Unknown key name ... ignoring` 并**静默忽略**，限流形同虚设。
> 改完用 `systemctl show qqbot -p StartLimitIntervalUSec` 验证真的生效（应输出 `10min`）。

## 十、安全组（阿里云）

入方向需放行：**9090**（后台）、**9091**（Webhook）、**9092**（静态页）。
- 授权对象填 `0.0.0.0/0`；**不要填自己当前公网 IP** —— 家宽/4G 是动态 IP，变了就连不上（曾踩过：记录 `39.70.214.115`，次日变 `39.70.5.150`）。
- 若只想本机访问，改 `settings.json` 的 `BIND_ADDR` 回 `127.0.0.1`，走 cloudflared 隧道访问。
- ⚠️ **WebUI 后台没有任何身份验证**，`BIND_ADDR=0.0.0.0` 等于把「开关插件、改配置、改玩家余额、关机器人」全部暴露给公网。仅用于自用测试，长期建议收回 `127.0.0.1` + 隧道。
