# -*- coding: utf-8 -*-
"""全局配置与路径解析。

本文件本身不存放任何密钥/凭据。所有敏感信息（机器人 AppID/Secret、
管理员 openid、协助者 openid、IP 白名单、本地龙图目录等）放在项目根
的 settings.json 里（该文件已被 .gitignore 忽略、不会上传到 GitHub）。
首次安装时把 settings.example.json 复制为 settings.json 并填入即可。

功能型常量保留代码默认值，也可在 settings.json 里覆盖。
本文件同时负责解析 bot 用到的外部可执行文件（BBDown / ffmpeg）：
优先使用项目内 tools/ 目录，其次旧路径，最后系统 PATH。
"""

import json
import glob as _glob
import os
import shutil
import sys

# 项目根目录：config.py 位于项目根，__file__ 所在目录即根目录。
# 其他模块需要定位项目内文件时统一从这里取，避免因文件搬家修改 dirname 层数。
ROOT = os.path.dirname(os.path.abspath(__file__))

# 项目内放置运行时下载的工具（BBDown / ffmpeg），不入库，由 install.ps1 首次下载
TOOLS_DIR = os.path.join(ROOT, "tools")

# 用户自研 / 扩展 meme 目录（位于项目内，由 install.ps1 拉取扩展并放置）
MEME_CUSTOM_DIR = os.path.join(ROOT, "bot", "meme", "custom_memes")


def _load_settings() -> dict:
    """读取 settings.json；不存在或损坏时返回空字典。"""
    fp = os.path.join(ROOT, "settings.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CFG = _load_settings()


def _cfg(key: str, default):
    """从 settings.json 取值，带代码默认值兜底。"""
    v = _CFG.get(key)
    return v if v not in (None, "") else default


# ---- 机器人凭据（必须放在 settings.json；默认留空，避免泄露）----
APPID = _cfg("APPID", "")
SECRET = _cfg("SECRET", "")

# bot 管理员 / 协助者 openid（来自 settings.json，不入库）
BOT_ADMINS = _cfg("BOT_ADMINS", [])
BOT_ASSISTANTS = _cfg("BOT_ASSISTANTS", [])

# 已在开放平台「IP 白名单」中添加过的公网 IP（后台提示用；from settings.json）
WHITELIST_IPS = _cfg("WHITELIST_IPS", [])

# 图库资源：由 install.ps1 从 GitHub「图库仓库」克隆到 resources/image_lib（不入库）
IMAGE_LIB_DIR = _cfg("IMAGE_LIB_DIR", os.path.join(ROOT, "resources", "image_lib"))

# 本地龙图目录：默认取图库仓库克隆后的 dragon/ 子目录（无需在 settings.json 手动填写）；
# 也可在 settings.json 的 DRAGON_DIR 显式覆盖
DRAGON_DIR = _cfg("DRAGON_DIR", os.path.join(IMAGE_LIB_DIR, "dragon"))


# ---- 功能型常量（可在 settings.json 覆盖）----
DEBUG = bool(_cfg("DEBUG", False))
MENU_KEYWORDS = _cfg("MENU_KEYWORDS", ["菜单", "帮助", "功能", "help"])
WEBUI_PORT = int(_cfg("WEBUI_PORT", 9090))
WEBHOOK_PORT = int(_cfg("WEBHOOK_PORT", 9091))

# 各 HTTP 服务的绑定地址。默认只绑回环（最安全）：
#   本机开发 + 内网穿透场景，只有 cloudflared 需要访问它们，绑 127.0.0.1 就够。
# 公网服务器上想直接用 http://<公网IP>:端口 访问，需改成 "0.0.0.0"
# （同时要在云安全组放行该端口，且强烈建议给 WebUI 设密码）。
BIND_ADDR = _cfg("BIND_ADDR", "127.0.0.1")
# 只想放开某一个服务时，可单独覆盖（留空表示沿用 BIND_ADDR）
WEBUI_BIND = _cfg("WEBUI_BIND", BIND_ADDR)
WEBHOOK_BIND = _cfg("WEBHOOK_BIND", BIND_ADDR)
STATIC_BIND = _cfg("STATIC_BIND", BIND_ADDR)

def _display_host(bind_addr: str, port: int) -> str:
    """把 0.0.0.0/:: 这类"监听全部"的地址，显示成可点击的本机回环地址。"""
    if bind_addr in ("0.0.0.0", "::", ""):
        return f"http://127.0.0.1:{port}"
    return f"http://{bind_addr}:{port}"

# 静态页面服务（网页测试等）：仅开放 bot/public_html/ 目录，公网地址为隧道域名
STATIC_PORT = int(_cfg("STATIC_PORT", 9092))
STATIC_PUBLIC_URL = _cfg("STATIC_PUBLIC_URL", "https://page.deemo8848.dpdns.org")

# 执行 meme worker / B站渲染子进程所用的 Python —— 见文件末尾统一解析
# （默认取当前解释器 sys.executable，避免 PATH 里没有 python 命令导致子进程失败）


# ---- 与 VPN / 代理客户端共存（实现见 bot/core/vpn_bypass.py）----
# 本机开 Clash 系客户端(猫猫云等)的 TUN 模式翻墙时，cloudflared 隧道出站流量
# 会被代理内核接管并可能被丢给代理节点，导致「bot 在线却收不到消息」。
# 开启后 bot 会自动往内核里补一组「cloudflared 直连」规则并定时巡检。
VPN_BYPASS_ENABLED = bool(_cfg("VPN_BYPASS_ENABLED", True))
MIHOMO_API = _cfg("MIHOMO_API", "http://127.0.0.1:9790")          # 代理内核控制接口
MIHOMO_CONFIG = _cfg("MIHOMO_CONFIG", "")                          # 留空则自动定位配置文件
VPN_BYPASS_INTERVAL = int(_cfg("VPN_BYPASS_INTERVAL", 600))        # 巡检间隔（秒）


# ---- 「吃什么」插件功能（均可覆盖至 settings.json）----
EAT_DEFAULT_FOODS = [
    "黄焖鸡米饭", "麻辣烫", "兰州拉面", "沙县小吃", "重庆小面", "螺蛳粉",
    "米线", "炒饭", "炒面", "盖浇饭", "水饺", "馄饨", "煎饼果子", "肉夹馍",
    "烤冷面", "关东煮", "冒菜", "香锅", "炸鸡", "汉堡", "披萨", "寿司",
    "便当", "凉皮", "凉面", "热干面", "酸辣粉", "炸酱面", "牛肉面", "叉烧饭",
    "烧腊饭", "煲仔饭", "石锅拌饭", "部队锅", "烤肉饭", "猪脚饭", "卤肉饭",
    "烤鸭饭", "口水鸡", "酸菜鱼", "水煮鱼", "毛血旺", "干锅", "烤鱼", "烧烤",
    "奶茶+面包", "便利店", "泡面", "食堂自选", "轻食沙拉",
]
EAT_TRIGGER_KEYWORDS = _cfg("EAT_TRIGGER_KEYWORDS", ["吃什么"])   # 触发关键词列表
EAT_SMART_CONTAINS = bool(_cfg("EAT_SMART_CONTAINS", False))     # 智能识别：关键词出现在文本任意位置即触发
EAT_RECOMMEND_PROBABILITY = float(_cfg("EAT_RECOMMEND_PROBABILITY", 0.3))  # 推荐 vs 复读概率
EAT_USE_BUILTIN = bool(_cfg("EAT_USE_BUILTIN", True))            # 是否启用内置食物库（可关掉只留自定义）
EAT_BUILTIN_FOODS = _cfg("EAT_BUILTIN_FOODS", EAT_DEFAULT_FOODS)  # 内置食物库（可整体覆盖为自定义清单）
EAT_CUSTOM_FOODS = _cfg("EAT_CUSTOM_FOODS", [])                   # 额外的自定义食物列表
EAT_RATE_LIMIT_ENABLED = bool(_cfg("EAT_RATE_LIMIT_ENABLED", True))  # 频率限制（防多Bot循环）
EAT_RATE_LIMIT_MAX = int(_cfg("EAT_RATE_LIMIT_MAX", 3))           # 每分钟最大响应次数
EAT_ECHO_COOLDOWN_ENABLED = bool(_cfg("EAT_ECHO_COOLDOWN_ENABLED", True))  # 复读冷却
EAT_ECHO_COOLDOWN_SECONDS = int(_cfg("EAT_ECHO_COOLDOWN_SECONDS", 15))      # 复读后若干秒内强制推荐
EAT_FOOD_IMAGES_DIR = _cfg("EAT_FOOD_IMAGES_DIR", "")              # 食物配图目录：放 "{食物}.jpg" 之类即可图文同发


# ---- 外部可执行文件解析：项目 tools/ → 旧路径 / 系统目录 → PATH ----
# 跨平台：Windows 找 *.exe，Linux 找无后缀文件。
# 注意 Windows 的旧硬编码路径只在 Windows 上尝试，Linux 上走 PATH 与常见安装位置。

_IS_WINDOWS = os.name == "nt"
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""

if _IS_WINDOWS:
    _BBDOWN_LEGACY = r"D:\略夹\BBd\BBDown.exe"
    _FFMPEG_LEGACY = r"D:\略夹\BBd\ffmpeg.exe"
    _BBDOWN_LEGACY_DIR = r"D:\略夹\BBd"
else:
    # Linux：优先用系统包管理器装的位置，其次 PATH
    _BBDOWN_LEGACY = "/usr/local/bin/BBDown"
    _FFMPEG_LEGACY = "/usr/bin/ffmpeg"
    _BBDOWN_LEGACY_DIR = "/usr/local/bin"

# Linux 上额外扫描这些目录（apt 装的 ffmpeg / 手动放的 BBDown）
_EXTRA_BIN_DIRS = [] if _IS_WINDOWS else ["/usr/bin", "/usr/local/bin", "/opt/ffmpeg/bin", "/snap/bin"]


def _find_in_tools(basename: str) -> str | None:
    """在项目 tools/ 下递归查找工具，按当前平台匹配后缀。"""
    cand = []
    if _EXE_SUFFIX:
        cand.append(basename + _EXE_SUFFIX)
    else:
        # Linux：先找无后缀，再兼容误放的 .exe
        cand += [basename, basename + ".exe"]
    for name in cand:
        m = _glob.glob(os.path.join(TOOLS_DIR, "**", name), recursive=True)
        # 过滤掉非文件与 Linux 下不可执行的
        for p in m:
            if os.path.isfile(p):
                return p
    return None


def _resolve_exe(override: str | None, name: str, legacy: str, fallback_cmd: str) -> str:
    if override and os.path.isfile(override):
        return os.path.abspath(override)
    in_tools = _find_in_tools(name)
    if in_tools:
        return os.path.abspath(in_tools)
    if legacy and os.path.isfile(legacy):
        return os.path.abspath(legacy)
    # 常见系统目录（Linux）
    for d in _EXTRA_BIN_DIRS:
        p = os.path.join(d, name + _EXE_SUFFIX)
        if os.path.isfile(p):
            return os.path.abspath(p)
    # PATH 查找：shutil.which 在 Windows 上对相对项可能返回 './x.EXE'，需校验真实存在
    w = shutil.which(name)
    if w and os.path.isfile(w):
        return os.path.abspath(w)
    # 都没找到：返回裸命令名，交给子进程按 PATH 解析（并在调用处做可用性检查）
    return fallback_cmd


BBDOWN_EXE = _resolve_exe(_cfg("BBDOWN_EXE", ""), "BBDown", _BBDOWN_LEGACY, "BBDown")
BBDOWN_DIR = os.path.dirname(BBDOWN_EXE) if os.path.sep in BBDOWN_EXE else ""
# BBDown 登录态（BBDown.data）的来源：用户手动登录用的 BBDown 所在目录。
# bot 运行时会把它同步到 BBDOWN_DIR，避免未登录导致解析受限；cookie 过期后重新登录即自动续期。
BBDOWN_COOKIE_SRC = _cfg("BBDOWN_COOKIE_SRC", _BBDOWN_LEGACY_DIR)
FFMPEG_EXE = _resolve_exe(_cfg("FFMPEG_EXE", ""), "ffmpeg", _FFMPEG_LEGACY, "ffmpeg")

# cloudflared 可执行文件（内网穿透），空表示未找到
CLOUDFLARED_EXE = _resolve_exe(_cfg("CLOUDFLARED_EXE", ""), "cloudflared", "", "cloudflared")

# meme 子进程 / 其他脚本用的解释器：默认取当前运行的 Python，比裸 "python" 更可靠
PYTHON = _cfg("PYTHON", sys.executable or "python")