# -*- coding: utf-8 -*-
"""Emoji 合成（等价于 Google Emoji Kitchen）。

移植自 astrbot 插件 astrbot_plugin_emojimix（MIT）：
  - 显式命令：发「emojimix 😀😂」合成两个 emoji 成一张图
  - 自动触发：只发送恰好两个 emoji 的消息时自动合成（可用 settings.json 的
    EMOJI_AUTO_TRIGGER=false 关闭）
图片来自 Google Emoji Kitchen 后端（gstatic），按日期码遍历找可用组合；
同组合结果缓存，命中即复用。素材为官方产物，非脆弱第三方 API。
"""

import asyncio
import io
import os
import time
import uuid

import aiohttp
import emoji
from PIL import Image

from config import ROOT, _cfg
from bot.commands import register, ROLE_ALL

# 供 Web 后台「其他功能 → emojimix」插件总开关使用的命令名集合
EMOJIMIX_CMD_NAMES = {"cmd_emojimix", "cmd_auto_emojimix"}

# Google Emoji Kitchen 后端日期代码（默认降序，优先较新的组合）
_DATE_CODES = [
    "20240204", "20250130", "20241023", "20241021", "20240715", "20240610",
    "20240530", "20240214", "20240206", "20231128", "20231113", "20230821",
    "20230818", "20230803", "20230426", "20230421", "20230418", "20230405",
    "20230301", "20230221", "20230216", "20230127", "20230126", "20221107",
    "20221101", "20220823", "20220815", "20220506", "20220406", "20220203",
    "20220110", "20211115", "20210831", "20210521", "20210218", "20201001",
]

_BASE_URL_TEMPLATE = \
    "https://www.gstatic.com/android/keyboard/emojikitchen/{date_code}/{hex1}/{hex1}_{hex2}.png"

_REQUEST_TIMEOUT = float(_cfg("EMOJI_REQUEST_TIMEOUT", 3.0))
_AUTO_TRIGGER = bool(_cfg("EMOJI_AUTO_TRIGGER", True))
_MAX_IMG = 10 * 1024 * 1024

_TMP = os.path.join(ROOT, "tmp", "emojimix")
os.makedirs(_TMP, exist_ok=True)

# (排序后的两个 emoji) -> 可用 URL，避免重复探测
_RESULT_CACHE = {}
_CACHE_TS = {}


def _extract_emojis(text: str):
    return [item["emoji"] for item in emoji.emoji_list(text or "")]


def _normalized(e1: str, e2: str):
    return (e1, e2) if e1 <= e2 else (e2, e1)


def _hexcode(e: str) -> str:
    return "-".join(f"u{ord(c):x}" for c in e)


def _candidate_urls(hex1: str, hex2: str):
    urls = []
    for dc in _DATE_CODES:
        urls.append(_BASE_URL_TEMPLATE.format(date_code=dc, hex1=hex1, hex2=hex2))
        if hex1 != hex2:
            urls.append(_BASE_URL_TEMPLATE.format(date_code=dc, hex1=hex2, hex2=hex1))
    return urls


async def _find_url(e1: str, e2: str):
    """遍历查找该组合的可用图片 URL，命中即缓存。找不到返回 None。"""
    key = _normalized(e1, e2)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    h1, h2 = _hexcode(e1), _hexcode(e2)
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    async with aiohttp.ClientSession() as s:
        for url in _candidate_urls(h1, h2):
            try:
                async with s.head(url, timeout=timeout, ssl=False) as resp:
                    status = resp.status
            except Exception:
                status = 0
            if status == 200:
                _RESULT_CACHE[key] = url
                _CACHE_TS[key] = time.time()
                if len(_RESULT_CACHE) > 256:
                    for k in [k for k, t in _CACHE_TS.items() if time.time() - t > 3600]:
                        _RESULT_CACHE.pop(k, None)
                        _CACHE_TS.pop(k, None)
                return url
    return None


async def _download_png(url: str) -> str:
    """把合成的 emoji 图拉到本地并保存为 PNG，返回本地路径。"""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=timeout, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            content = await resp.read()
    if len(content) > _MAX_IMG:
        raise RuntimeError("图片过大")
    path = os.path.join(_TMP, uuid.uuid4().hex + ".png")
    img = Image.open(io.BytesIO(content))
    img.save(path, format="PNG")
    img.close()
    return path


def _validate_pair(text: str):
    """从命令参数里解析出恰好两个 emoji，返回 (e1, e2, 错误提示)。"""
    if not (text or "").strip():
        return None, None, "🤔 请在命令后提供两个 Emoji。例如 `emojimix 😀😂`"
    emos = _extract_emojis(text)
    if len(emos) == 0:
        return None, None, "🤔 没检测到 Emoji，请提供两个 Emoji 喵"
    if len(emos) == 1:
        return None, None, "🤔 只有一个 Emoji，请提供两个 Emoji 喵"
    if len(emos) > 2:
        return None, None, "🤔 超过两个 Emoji 了，一次只合成两个喵"
    rest = text
    for e in emos:
        rest = rest.replace(e, "", 1)
    if (rest or "").strip():
        return None, None, f"🤔 除了两个 Emoji 外还有多余内容：'{rest.strip()}'"
    return emos[0], emos[1], None


async def _do_mix(ctx, e1: str, e2: str, reply: bool):
    url = await _find_url(e1, e2)
    if not url:
        await ctx.reply(f"😟 找不到 {e1} 和 {e2} 的混合 Emoji，可能这对组合不存在喵~")
        return
    try:
        path = await _download_png(url)
    except Exception:
        await ctx.reply("图片下载失败，请稍后再试喵~")
        return
    try:
        await ctx.sender.send_local_file(ctx.message, 1, path, reply=reply)
    except Exception:
        await ctx.reply("合成图片发送失败喵~")
    try:
        os.remove(path)
    except Exception:
        pass


def _cmd_matcher(text: str) -> bool:
    t = (text or "").strip()
    return t == "emojimix" or t.startswith("emojimix ")


@register(keywords=["emojimix"], help="合成两个emoji喵（emojimix 😀😂）",
          matcher=_cmd_matcher, role=ROLE_ALL)
async def cmd_emojimix(ctx):
    # 去掉命令前缀后解析 emoji
    text = (getattr(ctx.message, "content", None) or "")
    text = text.replace("emojimix", "", 1).strip()
    e1, e2, err = _validate_pair(text)
    if err:
        await ctx.reply(err)
        return
    await _do_mix(ctx, e1, e2, reply=True)


def _auto_matcher(text: str) -> bool:
    """仅当整条消息恰好是两个 emoji（无其他字符）时自动合成。"""
    if not _AUTO_TRIGGER:
        return False
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return False
    emos = _extract_emojis(t)
    if len(emos) != 2:
        return False
    rest = t
    for e in emos:
        rest = rest.replace(e, "", 1)
    return (rest or "").strip() == ""


@register(keywords=["__auto_emojimix__"], help="", matcher=_auto_matcher, role=ROLE_ALL)
async def cmd_auto_emojimix(ctx):
    text = (getattr(ctx.message, "content", None) or "")
    emos = _extract_emojis(text)
    if len(emos) < 2:
        return
    await _do_mix(ctx, emos[0], emos[1], reply=False)