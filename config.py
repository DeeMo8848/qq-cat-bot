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

# 执行 meme worker / B站渲染子进程所用的 Python（默认取 PATH 里的 python）
PYTHON = _cfg("PYTHON", "python")


# ---- 外部可执行文件解析：项目 tools/ → 旧路径 → PATH ----
_BBDOWN_LEGACY = r"D:\略夹\BBd\BBDown.exe"
_FFMPEG_LEGACY = r"D:\略夹\BBd\ffmpeg.exe"


def _find_in_tools(basename: str) -> str | None:
    pat = os.path.join(TOOLS_DIR, "**", basename)
    m = _glob.glob(pat, recursive=True)
    return m[0] if m else None


def _resolve_exe(override: str | None, name: str, legacy: str, fallback_cmd: str) -> str:
    if override and os.path.isfile(override):
        return override
    in_tools = _find_in_tools(name + ".exe")
    if in_tools:
        return in_tools
    if legacy and os.path.isfile(legacy):
        return legacy
    w = shutil.which(name)
    return w if w else fallback_cmd


BBDOWN_EXE = _resolve_exe(_cfg("BBDOWN_EXE", ""), "BBDown", _BBDOWN_LEGACY, "BBDown")
BBDOWN_DIR = os.path.dirname(BBDOWN_EXE) if os.path.sep in BBDOWN_EXE else ""
FFMPEG_EXE = _resolve_exe(_cfg("FFMPEG_EXE", ""), "ffmpeg", _FFMPEG_LEGACY, "ffmpeg")