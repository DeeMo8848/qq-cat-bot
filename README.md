<div align="center">

# 🐱 QQ 猫猫机器人

基于腾讯官方 SDK（`qq-botpy`）的本地 QQ 机器人，附带群闲聊、B站/多平台解析、表情包生成、随机图片 等 60+ 功能。

</div>

---

## ✨ 功能一览

| 模块 | 说明 |
| --- | --- |
| 💬 聊天 | 发「菜单 / 帮助 / 功能」「你好 / hi」等基础接入 |
| 📺 B站解析 | 发 B站链接 / BV号 → 自动解析封面+低画质视频；支持「下载视频 / 仅下载封面 / 仅下载音频 / BBDown 命令」 |
| 🌐 多平台解析 | 抖音、快手、小红书、微博、知乎、X(Twitter)、YouTube、TikTok、Xiaoheihe、NGA、网易云音乐、Steam、纯视频号 等链接解析 |
| 🎭 表情包(meme) | 预置 600+ 模板关键词（meme列表 / meme搜索 / meme更新 / meme刷新），支持 @、多个文本参数、GIF 模板 |
| 🖼️ 随机图片 | 多图源随机一图：UAPI、樱花、栗次元、随机兽耳酱、墨天逸、南风、Pixiv Yuki、Lolicon、本地龙图、随机小猪、随机奶龙 |
| 🤖 AI 对话 | 接入任意 OpenAI 兼容 API（DeepSeek / 硅基流动 / OpenAI …），可开关 |

> 完整命令清单见 [`命令清单.md`](./命令清单.md)；600+ meme 关键词见 [`meme_清单.md`](./meme_清单.md)。

---

## 🚀 快速开始（首次部署）

仓库只包含 bot 本体，**不含** BBDown、ffmpeg、meme-generator 等外部工具（太大）。
首次运行时用安装脚本自动检测、下载并放到项目内 `tools/` 与 Python 环境，无需手工准备。

### 1. 前置要求

- Windows + Python 3.10+（已加到 PATH）
- `git`（用于拉取 meme 扩展，可选）
- 一个已通过审核的 QQ 开放平台机器人（拿到 AppID / AppSecret）

### 2. 运行安装脚本

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会自动完成：
1. 检测 Python，安装 `requirements.txt` + `meme-generator==0.1.14`（固定版本，**勿升级到 0.2.x Rust 版**）
2. 下载 **BBDown 1.6.3** 到 `tools/BBDown/`
3. 下载 **ffmpeg** 到 `tools/ffmpeg/`（gyan.dev 失败自动换 BtbN）
4. 生成 `settings.json` 配置模板
5. 从扩展仓库拉取额外 meme 到 `bot/meme/custom_memes/`，并重建关键词数据

### 3. 配置凭据

编辑根目录的 `settings.json`（已自动生成）：

```json
{
  "APPID": "你的机器人 AppID",
  "SECRET": "你的机器人 AppSecret",
  "BOT_ADMINS": ["管理员 openid"],
  "BOT_ASSISTANTS": ["协助者 openid"],
  "WHITELIST_IPS": ["开放平台 IP 白名单里已加入的公网 IP"],
  "DRAGON_DIR": "本地龙图文件夹路径（随机龙用，留空则禁用）",
  "PYTHON": "meme/B站子进程用的 Python 路径（一般留空取 PATH）"
}
```

> ⚠️ `settings.json` 已被 `.gitignore` 忽略，**绝不会提交到仓库**。
> 仅配置格式参考 `settings.example.json`。

### 4. 启动

```bat
start.bat        # 或：python -u main.py
```

看到 `[OK] 机器人已上线` 即成功。之后在 QQ 私聊或群里 @机器人 即可。

### 5. 内网穿透（回调地址）

机器人回调地址需公网可达。项目内置 cloudflared 管理（`bot/core/tunnel.py`），
根目录放 `cloudflared.exe` + `tunnel/config.yml`（具名隧道）即可自动启动。
`cloudflared` 与隧道配置含本机凭据，已列入 `.gitignore`，需自行准备。

---

## 📦 项目结构

```
config.py               # 配置加载(从 settings.json) + 工具路径解析
settings.example.json   # 配置模板（真实文件 settings.json 不入库）
install.ps1             # 一键安装：检测/下载环境依赖
main.py                 # 入口：启动连接、监听事件、分发
start.bat / 启动bot.bat # 启动脚本（后者含本机路径，不入库）
requirements.txt        # Python 依赖
命令清单.md / meme_清单.md
tools/                  # BBDown、ffmpeg（由 install.ps1 下载，不入库）
bot/
  core/                 # 平台连接、webhook、tunnel、webui(?)、命令注册框架
  commands/             # 各功能命令（bilibili / meme / randomimg / ai 等）
  parse/                # 多平台链接解析（移植自 astrbot 解析插件）
  meme/                 # meme 生成框架封装 + worker + 关键词数据 + custom_memes
  ai/                   # AI 对话（OpenAI 兼容 API）
```

---

## 🔐 安全说明

- 所有凭据集中在 `settings.json`（被 git 忽略）；仓库不携带任何密钥。
- 本机专用启动脚本 `启动bot.bat`、运行时状态 `state.json`、日志、cloudflared 凭据
  均已在 `.gitignore` 中排除，不会上传。
- 本地龙图目录在 `settings.json` 的 `DRAGON_DIR` 配置，不在仓库内。

---

## 🙏 致谢与参考

本项目在开发过程中参考、移植了大量优秀开源项目，特此致谢：

### 核心工具

- **[BBDown](https://github.com/nilaoda/BBDown)** —— 强大的 B 站下载器，B站解析核心引擎。
- **[meme-generator](https://github.com/MemeCrafters/meme-generator)** —— 表情包生成框架，
  预置数百个一流 meme 模板（锁 **0.1.14** 版，0.2.x 为 Rust 重写版、接入不同）。

### meme 模板扩展

- [meme_emoji](https://github.com/anyliew/meme_emoji)
- [meme-generator-contrib](https://github.com/MemeCrafters/meme-generator-contrib)
- [crazy_emoji](https://github.com/anyliew/crazy_emoji)
- [meme-demo](https://github.com/USYDShawnTan/meme-demo)

### 移植 / 参考的 astrbot 插件

- **astrbot_plugin_handsign_memes** —— 「肥鱼举牌」meme（`bot/meme/custom_memes/feiyu/`）
  移植自该项目（原插件依赖 sketchbook，这里用 pil_utils/PIL 重写了文字自适应）
- **astrbot_plugin_SteamLink** —— Steam 商店解析器（`bot/parse/parsers/steam.py`）
- **astrbot_plugin_bili_resolver** —— 《B站解析》链接 meta 提取逻辑参考其做法（`bot/core/webhook.py`）
- **astrbot 链接解析插件生态** —— `bot/parse/` 多平台解析的分层结构（Parser / Downloader / Renderer / Sender、EmojiLikeArbiter）源自 astrbot 解析插件框架

### 其他

- 腾讯官方 [qq-botpy](https://github.com/tencent-connect/botpy) SDK，以及活跃的 QQ 机器人开源社区。

无以上优秀项目，就没有这个机器人。真心感谢所有作者 ❤️

---

## 📄 许可证

本项目仅供个人学习与研究使用。引用第三方工具/插件请遵循其各自的许可证。