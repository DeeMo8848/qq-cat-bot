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

仓库只包含 bot 本体，**不含** BBDown、ffmpeg、meme-generator 等外部工具（太大），也不含龙图/批量 meme 素材。
这些资源由两个独立的 GitHub 资源仓库承载，首次安装时由 `install.ps1` 自动拉取，无需手工准备。

| 资源 | 承载仓库 | 落地位置 | 说明 |
| --- | --- | --- | --- |
| 🐉 龙图(随机龙) | 图库仓库 `qq-cat-image-lib` | `resources/image_lib/dragon/` | 龙图作为 `dragon/` 次级目录存放 |
| 🎭 批量 meme 模板 | meme 聚合仓库 `qq-cat-memes` | `bot/meme/custom_memes/`（经子模块拉源仓库） | 聚合仓库引用 4 个公开源仓库 |
| 🛠️ BBDown / ffmpeg / meme-generator | 各官方源 | `tools/` 与 Python 环境 | 首次自动下载 |

### 1. 前置要求

- Windows + Python 3.10+（已加到 PATH）
- `git`（拉取图库与 meme 素材必需）
- 一个已通过审核的 QQ 开放平台机器人（拿到 AppID / AppSecret）
- 两个资源仓库为**私有**仓库，拉取需一个 GitHub 令牌（PAT，勾选 `repo` 权限）

### 2. 运行安装脚本

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -GitToken "ghp_你的令牌"
```

> 若 `git` 已登录且对私有仓库有权限，可省略 `-GitToken`；不带令牌时图库与 meme 聚合仓库会改用公开源仓库回退拉取。

脚本会自动完成：
1. 检测 Python，安装 `requirements.txt` + `meme-generator==0.1.14`（固定版本，**勿升级到 0.2.x Rust 版**）
2. 下载 **BBDown 1.6.3** 到 `tools/BBDown/`
3. 下载 **ffmpeg** 到 `tools/ffmpeg/`（gyan.dev 失败自动换 BtbN）
4. 生成 `settings.json` 配置模板
5. 克隆**图库仓库**到 `resources/image_lib/`（龙图目录自动使用其 `dragon/` 子目录）
6. 拉取 **meme 聚合仓库**（优先含子模块，失败回退直接克隆源仓库）并装载到 `bot/meme/custom_memes/`
7. 重建 meme 关键词数据

### 3. 配置凭据

编辑根目录的 `settings.json`（已自动生成）：

```json
{
  "APPID": "你的机器人 AppID",
  "SECRET": "你的机器人 AppSecret",
  "BOT_ADMINS": ["管理员 openid"],
  "BOT_ASSISTANTS": ["协助者 openid"],
  "WHITELIST_IPS": ["开放平台 IP 白名单里已加入的公网 IP"],
  "PYTHON": "meme/B站子进程用的 Python 路径（一般留空取 PATH）"
}
```

> 💡 **龙图目录无需手动填**：`config.py` 默认取 `resources/image_lib/dragon/`（由第 5 步从图库仓库拉取）。
> 仅当你想手工换目录时才在 `settings.json` 里加 `"DRAGON_DIR": "你的龙图目录"`（这样会覆盖自动路径）。
> ⚠️ `settings.json` 已被 `.gitignore` 忽略，**绝不会提交到仓库**。
> 仅配置格式参考 `settings.example.json`。

### 4. 启动

```bat
start.bat        # 或：python -u main.py
```

看到 `[OK] 机器人已上线` 即成功。之后在 QQ 私聊或群里 @机器人 即可。

> 💡 **免 @ 与解锁完整功能**：若在 QQ 开放平台为机器人开启 **「主动消息 (active message)」** 同时也开启 **「全量消息 (完整消息内容)」**，
> 群内即可**无需 @ / 免 @机器人** 触发命令（默认也放开了「仅管理员」限制，任意群成员都可触发）；同时解锁需要完整消息内容的进阶功能。
> 若不开启，则只能靠 @机器人 使用基础命令。

### 5. 内网穿透（回调地址）

机器人回调地址需公网可达。项目内置 cloudflared 管理（`bot/core/tunnel.py`），
根目录放 `cloudflared.exe` + `tunnel/config.yml`（具名隧道）即可自动启动。
`cloudflared` 与隧道配置含本机凭据，已列入 `.gitignore`，需自行准备。

---

## 📦 项目结构

```
config.py               # 配置加载(从 settings.json) + 工具路径解析
settings.example.json   # 配置模板（真实文件 settings.json 不入库）
install.ps1             # 一键安装：检测/下载环境依赖 + 拉取图库/meme 资源
main.py                 # 入口：启动连接、监听事件、分发
start.bat / 启动bot.bat # 启动脚本（后者含本机路径，不入库）
requirements.txt        # Python 依赖
命令清单.md / meme_清单.md
tools/                  # BBDown、ffmpeg（由 install.ps1 下载，不入库）
resources/              # 图库克隆(龙图等)，由 install.ps1 拉取，不入库
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
- 龙图/批量 meme 素材由 `install.ps1` 从独立的图库仓与 meme 聚合仓按需拉取，
  不在本仓库内；`.gitignore` 已排除 `resources/` 与 `custom_memes/`（`feiyu/` 本地移植插件除外）。

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