# 🐧 Linux 部署指南

把 QQ 猫猫机器人部署到 Linux 服务器（含阿里云 2 核 2G 等小内存机器）。
同一份代码在 Windows 和 Linux 上都能跑，本目录只放 Linux 侧脚本。

---

## 一、快速开始（三步）

```bash
# 1. 拉取代码
git clone https://github.com/DeeMo8848/qq-cat-bot.git
cd qq-cat-bot

# 2. 一键准备环境（装依赖 + 下载 BBDown/ffmpeg/cloudflared + 拉素材）
bash deploy/install.sh

# 3. 填凭据后启动
nano settings.json          # 填 APPID / SECRET / BOT_ADMINS
bash deploy/start.sh        # 前台启动；--daemon 后台启动
```

看到控制台打印「机器人已上线」就成功了。

---

## 二、目录里的脚本

| 脚本 | 作用 | 何时用 |
|---|---|---|
| `install.sh` | 一次性环境准备：依赖、三个外部工具、素材、配置模板 | 首次部署 |
| `start.sh` | 启动 / 停止机器人 | 日常 |
| `watchdog.sh` | **进程守护**：bot 挂了自动拉起。**手动启停** | 需要崩溃自愈时 |
| `service.sh` | 注册 systemd 服务：开机自启 + 崩溃重启 | 推荐服务器用 |

---

## 三、启动与停止

```bash
bash deploy/start.sh              # 前台运行（Ctrl+C 停止）
bash deploy/start.sh --daemon     # 后台运行，日志写入 logs/bot.log
bash deploy/start.sh --restart    # 先停再起
bash deploy/start.sh stop         # 停止

tail -f logs/bot.log              # 跟踪日志
```

---

## 四、开机自启（推荐）

`systemd` 比 shell 守护更可靠，**二选一即可**：

```bash
sudo bash deploy/service.sh install     # 安装并启动服务
systemctl status qqbot                  # 查看状态
systemctl stop qqbot                    # 停止（不会被自动拉起）
systemctl restart qqbot                 # 重启
journalctl -u qqbot -f                  # 查看日志
sudo bash deploy/service.sh uninstall   # 卸载
```

服务已配置 `Restart=always`，程序崩溃 10 秒后自动重启；
10 分钟内崩溃超过 5 次会停止重试，避免无限刷屏。

---

## 五、进程守护（可选，手动启停）

不想用 systemd 的话，可以用独立的守护脚本。

> ⚠️ **这个脚本不会随 bot 启动，必须手动开启。**
> 这样你主动关掉 bot 时，不会立刻被它重新拉起来。

```bash
bash deploy/watchdog.sh start      # 开启守护（后台）
bash deploy/watchdog.sh status     # 查看守护与 bot 状态
bash deploy/watchdog.sh stop       # 关闭守护（同时停掉 bot）
bash deploy/watchdog.sh log        # 查看最近 50 行守护日志
```

可调参数（环境变量）：

```bash
INTERVAL=60 MAX_FAILS=5 bash deploy/watchdog.sh start
```

- `INTERVAL`：检查间隔秒数，默认 30
- `MAX_FAILS`：连续失败多少次才重启（防抖动），默认 3

> `watchdog.sh stop` 会**同时停掉守护和 bot**，这是刻意设计——
> 只停守护不停 bot 会让你以为「已经关了」但进程还在。

---

## 六、远程更新（bot更新 / 喵喵更新）

在 QQ 里给 bot 发 **`bot更新`** 或 **`喵喵更新`**（仅管理员 / 协助者），它会：

1. `git fetch` 检查 GitHub 有没有新提交
2. 有更新 → 检查本地有无未提交改动（有则中止，避免覆盖你的修改）
3. `git pull --ff-only` 拉取
4. 若 `requirements.txt` 变了 → 自动 `pip install`
5. 回复更新摘要 → **自动重启**

发 **`bot版本`** 可以只看版本、不做任何修改。

### 重启机制（两平台不同）

| 平台 | 方式 | 说明 |
|---|---|---|
| **Linux** | `os.execv` 原地替换进程 | PID 不变，systemd 无感，最干净 |
| **Windows** | `os._exit` 退出 | 需由外部拉起，见下 |

因为 Windows 没有 `execv`，更新后进程会退出，需要外部把 bot 拉回来：

- **推荐**：把 `run-loop.bat` 设为开机启动（它会在 bot 退出后自动重启）
- 或：手动再跑一次 `start.bat`
- 如果你用 systemd / supervisor，Linux 侧会自动拉起，无需额外操作

> Windows 的 `start.bat` 保持「退出即停」不变，方便你想关就关；
> 自动重启的职责交给独立的 `run-loop.bat`。

---

## 七、外部工具说明

bot 用到三个外部工具，`install.sh` 会自动下载到 `tools/` 与项目根：

| 工具 | 用途 | 安装位置 |
|---|---|---|
| **BBDown** | B站视频解析 | `tools/BBDown/BBDown` |
| **ffmpeg** | 视频/音频处理 | `tools/ffmpeg/ffmpeg` |
| **cloudflared** | 内网穿透（Webhook 回调） | `./cloudflared` |

脚本会自动识别 `x86_64` / `arm64` 架构下载对应版本。

### 如果下载失败

国内服务器访问 GitHub 可能较慢，可手动下载后放到对应位置：

```bash
# cloudflared
curl -L -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared

# 系统自带的 ffmpeg 也能直接用
apt install -y ffmpeg
```

也可以在 `settings.json` 里显式指定路径：

```json
{
  "BBDOWN_EXE": "/usr/local/bin/BBDown",
  "FFMPEG_EXE": "/usr/bin/ffmpeg",
  "CLOUDFLARED_EXE": "/usr/local/bin/cloudflared"
}
```

---

## 八、2 核 2G 小内存优化建议

默认配置在 2G 内存上可以跑，但建议做几项调整：

### 1. 加 swap（内存不足时救命）

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h    # 确认生效
```

### 2. 关掉用不到的功能

在 Web 后台（`http://127.0.0.1:9090`）关掉不需要的插件开关。
如果用不到内网穿透（只用 WebSocket 模式），可以不装 cloudflared。

### 3. 限制 pip 缓存

```bash
pip config set global.no-cache-dir true
```

### 4. 容器/服务内存限制（systemd）

编辑 `/etc/systemd/system/qqbot.service`，在 `[Service]` 段加：

```ini
MemoryMax=1500M
```

然后 `sudo systemctl daemon-reload && sudo systemctl restart qqbot`。

---

## 九、常见问题

**Q：提示「未找到装有 botpy 的 Python」**
```bash
python3 -m pip install -r requirements.txt
```

**Q：cloudflared 起不来，bot 收不到消息**
看 `cloudflared.log`。内网穿透依赖具名隧道配置（`tunnel/config.yml` 与证书），
这部分文件不随仓库分发，需要从原机器拷过去，或改用纯 WebSocket 模式。

**Q：想让 bot 在后台一直跑，关掉 SSH 也不停**
用 `bash deploy/start.sh --daemon`（内部用 `setsid`），或直接用 systemd。

**Q：`bot更新` 提示连不上 GitHub**
服务器网络问题。可配置代理，或手动在服务器上 `git pull` 后重启。

**Q：更新后命令没生效**
Linux 下 `bot更新` 用 `execv` 原地重启，应当立即生效。
Windows 下需确认 `run-loop.bat` 在运行，否则进程退出后不会自动起来。

---

## 十、与 Windows 版本的对应关系

| Windows | Linux | 说明 |
|---|---|---|
| `install.ps1` | `deploy/install.sh` | 环境准备 |
| `启动bot.bat` / `start.bat` | `deploy/start.sh` | 启动机器人 |
| `run-loop.bat` | `deploy/watchdog.sh` | 崩溃自动重启 |
| 无（用任务计划） | `deploy/service.sh` | 开机自启（systemd） |

跨平台差异集中在 `bot/core/platform.py` 一处，其余代码不区分系统。
