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
tail -f /www/wwwlogs/qqbot.deemo8848.dpdns.org.log   # nginx 访问日志（公网入口）
tail -f /root/qq-cat-bot/cloudflared.log    # 隧道日志（隧道已弃用，仅在 TUNNEL_ENABLED=true 时有内容）

bash deploy/start.sh --restart    # 不走 systemd 时的重启方式
```

## 三、端口 / 域名

| 端口 | 用途 | 公网域名 |
|---|---|---|
| 9090 | WebUI 后台 | 仅本机（`BIND_ADDR=0.0.0.0`，切勿随意暴露） |
| 9091 | Webhook 回调 | `https://qqbot.deemo8848.dpdns.org`（nginx 反代） |
| 9092 | 静态页（`bot/public_html/`） | `https://page.deemo8848.dpdns.org`（nginx 反代） |
| 8082/8083 | 预留 proj1/proj2 | 未启用（需要时见第五节） |

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

### 6. pycairo（5000兆等渐变 meme 需要）
```bash
cd /root/qq-cat-bot && .venv/bin/pip install pycairo
```
> 缺它时带渐变/描边的 meme（如 `5000choyen`）会直接报
> 「缺少必要的依赖库 'pycairo'」。容器里 cairo 开发库本来就有，源码编译即可。

### 7. ★ 中文字体不用装 —— 项目自带字体包

`bot/assets/fonts/` 是**入库的项目资产**，随仓库分发，服务器**无需安装任何中文字体**：

| 文件 | 内容 | 体积 |
|---|---|---|
| `qqbot-fonts.ttc` | 中文字体集合，含 21 个族名别名（`Noto Sans SC` / `FZShaoEr-M11S` 等） | 3.6MB |
| `NotoColorEmoji.ttf` | 彩色 emoji（Noto Color Emoji，OFL-1.1） | 10.2MB |

启动时 `main.py` 会自动调用 `bot.core.fonts.install()` 把它注入 Skia 的
全局 FontManager，供所有**按族名查字体**的渲染路径（meme 等）使用。

> **为什么必须自带**：`meme_generator` 一个字体文件都不带，127 个模板硬编码引用
> `FZShaoEr-M11S`(72个) / `FZXS14`(27) 等族名；干净的 Linux 服务器上 Skia 只有
> `Noto Sans` 一个拉丁族 → 匹配不到 → 落到无 CJK 字形的兜底字体 → **文字全是「口口口」**。
>
> **重新生成字体包**：`python bot/meme/build_font_bundle.py`
> **自检**：`python -c "from bot.core import fonts; print(fonts.families())"` 应列出 22 个族名。

### 8. ★ 项目级共享 venv（`tools/.venv`）

给「需要独立依赖环境的工具」复用，目前只有 **cardforge**（rembg + onnxruntime）。

```bash
ls -d /root/qq-cat-bot/tools/.venv            # 应存在
/root/qq-cat-bot/tools/.venv/bin/python -c "import rembg, onnxruntime; print('OK')"
```

- **位置固定** `tools/.venv`（跨平台），由 `bot/core/venv.py` 统一解析，不入库
- cardforge 通过环境变量 `CARDFORGE_VENV` 或它自己 `settings.json` 的 `venv` 字段指过来，
  **不在它自己目录里另建一份**
- `deploy/install.sh` 会在克隆 cardforge 后自动创建并装依赖；
  已装过则跳过（用 `import rembg, onnxruntime` 探测）
- 手动重建：
  ```bash
  cd /root/qq-cat-bot
  python3 -m venv tools/.venv
  tools/.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r tools/cardforge/requirements.txt
  ```
- **抠图模型约 2GB 不在仓库里**，rembg 首次抠图时自动下载到 `tools/cardforge/models/`

### 9. ★★ 抠图内存红线（务必先看这一节）

> **事故记录（2026-09-21）**：在一台 **1.6GB 内存**的 ECS 上跑 cardforge 的默认抠图模型
> BiRefNet-lite（权重 130MB），**整机被拖入 OOM 僵死** —— TCP 端口全部能连上，
> 但 SSH/HTTP 一律零响应。
>
> ★ **这种僵死不会自行恢复。** 干等没用（实测等了数分钟毫无好转），
> 最终是**登录阿里云控制台强制重启整台服务器**才恢复的。
> 所以代价不是「慢一下」，而是「服务彻底中断 + 必须人工救场」，
> 严重程度远超预期 —— 这也是为什么本例宁可降级/改走 API，也绝不硬跑。
>
> 教训：**ONNX 抠图模型的内存需求 ≠ 权重体积**。载入权重后还要分配若干倍中间张量，
> 输入图越大倍数越高。1.6GB 的机器连 130MB 的「lite」都扛不住。

**部署前先量内存：**

```bash
awk '/MemAvailable/{printf "可用内存 %.0f MB\n", $2/1024}' /proc/meminfo
```

| 可用内存 | 结论 |
|---|---|
| **≥ 2.5GB** | 本地抠图（BiRefNet-lite）可用 |
| **1.2 ~ 2.5GB** | 勉强可用，须避开超大图；建议改用抠图 API |
| **< 1.2GB** | **别用本地抠图**。用抠图 API，或强制 `"model": "none"`（不抠图，整图当素材） |

**两条防线：**

1. **运行时自动降级**（`tools/cardforge/engine.py` 的 `_mem_guard`）
   加载模型前读 `/proc/meminfo` 的 `MemAvailable`，不够就沿
   `birefnet-general-lite → silueta → u2netp → none` 依次降级，
   并在日志里打印实际选用结果。内存极低时自动走「不抠图」直通，**不会硬跑把机器打死**。
2. **部署期提醒**（`deploy/install.sh` 第 6 步(附)）
   装依赖前打印内存结论，提前让人决定是否配置抠图 API。

**在 `tools/cardforge/settings.json` 配置抠图 API（推荐给小内存机器）：**

```json
{
  "matting_api": {
    "access_key_id": "<阿里云 AK>",
    "access_key_secret": "<阿里云 SK>"
  }
}
```

配好后 `engine_name` 传 `api` 即走阿里云分割抠图，本地零模型、零内存压力。

## 五、公网入口（A 记录 + 宝塔 nginx 反代）

> **2026-09-24 变更**：原先用 Cloudflare 隧道（cloudflared）暴露 9091/9092，
> 后因 **Cloudflare 收紧 `cfargotunnel.com` 解析**（只返回内部 IPv6 ULA `fd10::`，
> 不再返回公网 IPv4 边缘 IP）+ **隧道要求域名 NS 必须托管在 Cloudflare**
> （本域 NS 在 DigitalPlat，属第三方 DNS，官方明确不支持 Free 版这种用法），
> 隧道彻底不可用。**已改为「A 记录直连 + 宝塔 nginx 反代」**，与 `minigame` 一致。
> bot 侧也默认关闭了隧道启动（`settings.json` 的 `TUNNEL_ENABLED`，默认 false）。

### 域名 → 后端 映射

| 域名 | 后端端口 | 用途 |
|---|---|---|
| `qqbot.deemo8848.dpdns.org` | 127.0.0.1:9091 | Webhook 回调（腾讯推送全量群消息） |
| `page.deemo8848.dpdns.org` | 127.0.0.1:9092 | 静态页（`bot/public_html/`） |
| `minigame.deemo8848.dpdns.org` | 127.0.0.1:9094 | 五子棋平台 |

DNS 侧：均为 **A 记录指向 `47.83.166.37`**（在 DigitalPlat 面板维护）。
★ DigitalPlat 的 CNAME 值**末尾必须带点**（FQDN），否则面板会当相对名并追加 zone 名。

### nginx 配置位置

站点由宝塔创建，**反代写在站点级扩展目录**（面板重写站点 conf 也不会冲掉）：

```
/www/server/panel/vhost/nginx/extension/<域名>/proxy.conf
```

内容形如（保留 `/.well-known` 供证书验证，其余整站反代）：
```nginx
location ^~ /.well-known/ { allow all; root /www/wwwroot/<域名>; }
location ^~ / {
    proxy_pass http://127.0.0.1:9091;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 50m;
}
```

### 验证（不依赖 DNS，改完立刻可测）

```bash
# 反代是否通：后端自身的响应应该出现
curl -s -H 'Host: qqbot.deemo8848.dpdns.org' http://127.0.0.1/     # → webhook ok
# SNI 路由返回的证书对不对
echo | openssl s_client -connect 127.0.0.1:443 -servername qqbot.deemo8848.dpdns.org 2>/dev/null \
  | openssl x509 -noout -subject -dates
```
> ★ `nginx -s reload` 是**异步平滑重启**：紧接着 reload 的第一次请求可能仍命中旧
> worker（表现为"配置明明写对了却返回旧页面"）。**稍等 1~2 秒再验。**

### 证书（Let's Encrypt，acme.sh 自动续期）

```bash
# 申请 + 安装（webroot 模式，走站点的 /.well-known/）
/root/.acme.sh/acme.sh --issue -d <域名> --webroot /www/wwwroot/<域名> \
  --server letsencrypt --keylength ec-256
/root/.acme.sh/acme.sh --install-cert -d <域名> --ecc \
  --key-file /www/server/panel/vhost/cert/<域名>/privkey.pem \
  --fullchain-file /www/server/panel/vhost/cert/<域名>/fullchain.pem \
  --reloadcmd "/www/server/nginx/sbin/nginx -s reload"
```
续期由 acme.sh 的全局 cron 统一负责（`59 5,11,17,23 * * * acme.sh --cron`）。

### 新增子域名（两条现成命令）

```bash
/www/server/panel/pyenv/bin/python /root/_setup_proxy_site.py <域名> <后端端口> "备注"
/root/qq-cat-bot/.venv/bin/python /root/_enable_https_domain.py <域名>
```
第一条：宝塔建站 + 写扩展配置 + `nginx -t` + reload（幂等，带备份与失败回滚）；
第二条：申请证书 → 安装 → 加 `listen 443 ssl` + SSL 段 → reload。
前置条件：DNS 里已有指向 `47.83.166.37` 的 **A 记录**（证书的 HTTP 文件验证要用）。

### 附：cloudflared 隧道（已弃用，保留备查）

`tunnel/config.yml` 与 `/root/.cloudflared/<uuid>.json` 仍在，但**默认不再启动**
（`TUNNEL_ENABLED=false`）。若将来域名 NS 迁到 Cloudflare，可在 `settings.json` 里
打开它。**注意：隧道要求域名 NS 托管在 Cloudflare，否则配了也不生效。**

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
| `bot/assets/fonts/` | **内置字体包**（中文 TTC + 彩色 emoji） | **在仓库里**（`git pull` 即到） |
| `resources/image_lib/dragon/` | 龙图 189 张 | 本机打包上传（不在仓库） |
| `bot/meme/custom_memes/feiyu/` | 自研 meme 插件 | 同上（仓库里已带） |
| `bot/meme/custom_memes/_sources/` | meme 扩展模板（465 个） | `install.sh` 从 qq-cat-memes 克隆 |
| `tools/{BBDown,ffmpeg,cloudflared}` | 外部二进制 | install.sh 下载 / 复制自 `/usr/local/bin` |
| `tools/cardforge/` | 卡牌制作工具（含素材） | `install.sh` 从 cardforge 仓库克隆 |
| `tools/.venv/` | **项目级共享 venv** | `install.sh` 创建（不在仓库） |
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
- ★ **推荐现状**：公网入口交给 nginx 反代（反代目标是 `127.0.0.1:9091/9092`），
  所以 `settings.json` 里把 `BIND_ADDR` 设成 **`127.0.0.1`** 最安全 —— 9091/9092 不再
  直接对公网开放，只留 80/443 由 nginx 统一收口。若确实需要公网直连端口，再改回 `0.0.0.0`。
- ⚠️ **WebUI 后台没有任何身份验证**，`BIND_ADDR=0.0.0.0` 等于把「开关插件、改配置、
  改玩家余额、关机器人」全部暴露给公网。若只想让它本机可见，单独设 `WEBUI_BIND=127.0.0.1`。
