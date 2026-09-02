# -*- coding: utf-8 -*-
"""「下载图片」命令：把用户引用/回复的那张图（或表情包）下载下来，
以普通图片重新发出，方便 PE 端长按保存。

QQ 端很多表情 PE 上无法直接长按保存；用本命令让机器人把原图以
标准图片消息重发一遍，即可正常保存。

命令：
  下载图片 / 下载表情     —— 需当前消息引用/回复了带图的消息（或直接带图）
"""

import logging
import os
import re
import time
import uuid

import aiohttp

from config import ROOT
from bot.commands import register, ROLE_ALL

_log = logging.getLogger("downloadimg")

_TMP_DIR = os.path.join(ROOT, "tmp", "downloadimg")
os.makedirs(_TMP_DIR, exist_ok=True)

_TIMEOUT = aiohttp.ClientTimeout(total=40)

_BROWSER_HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}

_COM_EXT = ("jpg", "jpeg", "png", "gif", "webp", "bmp")


def _deep_image_urls(message):
    """从消息里深度提取图片 URL。

    引用/回复消息时，被引用旧消息的图片会「嵌」在当前消息 msg_elements 的
    嵌套元素里（type=reply/file/image/emoji），需逐层挖出来；普通附件则直接取。
    """
    urls = list(getattr(message, "image_urls", None) or [])
    seen = set(u for u in urls if isinstance(u, str))

    def _take(u):
        if isinstance(u, str) and u.startswith("http") and u not in seen:
            seen.add(u)
            urls.append(u)

    for att in (getattr(message, "attachments", None) or []):
        if isinstance(att, dict):
            _take(att.get("url"))
            _take(att.get("file_url"))
        else:
            _take(getattr(att, "url", None))

    def walk(node):
        if isinstance(node, dict):
            content = node.get("content")
            if isinstance(content, dict) and content.get("file_type") == 1:
                _take(content.get("url"))
                _take(content.get("file_url"))
            for k in ("url", "file_url", "cover_url"):
                _take(node.get(k))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(getattr(message, "msg_elements", None))
    return urls


async def _fetch_bytes(url):
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.get(url, timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


def _guess_ext(url, data):
    """按 URL 后缀或文件头猜测扩展名。"""
    m = re.search(r"\.([A-Za-z0-9]{2,5})$", (url or "").split("?")[0] or "")
    if m and m.group(1).lower() in _COM_EXT:
        return m.group(1).lower()
    head = data[:16]
    if head[:2] == b"\xff\xd8":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return "png"


@register(keywords=["下载图片", "下载表情"], role=ROLE_ALL, exact=True,
          help="引用一张图片/表情后发送，下载原图便于长按保存喵")
async def cmd_download_image(ctx):
    urls = _deep_image_urls(ctx.message)
    if not urls:
        return await ctx.reply("请引用/回复一张图片（或表情包）再发「下载图片」喵")
    url = urls[0]
    data = await _fetch_bytes(url)
    if not data:
        return await ctx.reply("图片下载失败喵，请重试")
    ext = _guess_ext(url, data)
    path = os.path.join(_TMP_DIR, f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.{ext}")
    with open(path, "wb") as f:
        f.write(data)
    _log.info("已下载图片(%dB) -> %s", len(data), path)
    await ctx.sender.send_local_file(ctx.message, 1, path, reply=False)