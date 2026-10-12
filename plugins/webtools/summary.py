# -*- coding: utf-8 -*-
"""链接总结：把「引用消息里的链接」抓成正文，交给 AI 出一段摘要。

触发方式是**关键词模糊匹配**（不是自动总结，也不是精确匹配）——
用户引用一条带链接的消息，再说一句「帮我总结下这个」即可。
触发词刻意取长（≥4 字，如「帮我总结」而非「总结」），避免日常闲聊误触发。

取链接的难点：被引用的消息在 webhook 事件里是一棵 `msg_elements` 树，引用层会再嵌套
一层，而里面的原文可能被塞在 `content` 这种 **JSON 字符串**里。所以这里不逐字段猜测
结构，而是把整棵树序列化成文本后统一正则抓 URL —— 更耐得住平台改版。

正文提取：服务器上已有 beautifulsoup4 + lxml，本地解析即可，零新增依赖；
抓不到正文（JS 渲染页）时再走 r.jina.ai 兜底（免费、无需 key、20 请求/分钟）。
"""

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import aiohttp

from config import (LINK_SUMMARY_JINA_FALLBACK, LINK_SUMMARY_MAX_CHARS,
                    LINK_SUMMARY_TIMEOUT)

_log = logging.getLogger("webtools.summary")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 引用消息的原文常被塞进 content（JSON 字符串），转义后会带反斜杠，所以把 \ 也排除掉
_URL_RE = re.compile(r'''https?://[^\s<>"'\）)】\]，,。；;、\\]+''', re.I)
_TRIM = ".,;:!?)]}\"'、。，；）\\"
# QQ 自己的附件/CDN 域与常见媒体后缀：这些不是「分享的链接」，要排掉
_SKIP_HOSTS = ("multimedia.nt.qq.com.cn", "gchat.qpic.cn", "qpic.cn",
               "qq.com", "gtimg.cn", "hdslb.com", "bilivideo.com",
               "bilivideo.cn", "bilivideo.net")
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
             ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac",
             ".mp4", ".mov", ".avi", ".mkv", ".zip", ".rar", ".7z", ".apk")

_SUMMARY_SYS = (
    "你是 QQ 群里的资料助手，擅长把网页内容压缩成好读的中文摘要。\n"
    "输出格式（纯文本：不要星号加粗、不要表格、不要代码块）：\n"
    "📄 网页摘要\n"
    "<页面标题>\n"
    "【一句话】用一句话说清这篇讲什么\n"
    "【要点】\n• 要点1\n• 要点2\n• 要点3（最多 5 条）\n"
    "要求：只依据给到的正文，不要编造；拿不到的信息就不写；总长不超过 500 字。"
)


def _walk(elements):
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        yield el
        yield from _walk(el.get("msg_elements"))


def _host_skipped(host):
    h = (host or "").lower().rstrip(".")
    for d in _SKIP_HOSTS:
        if h == d or h.endswith("." + d):
            return True
    return False


def collect_urls(message, text=""):
    """从「消息正文 + 引用消息」里抓出候选链接（去重保序，最多 3 条）。"""
    chunks = [text or ""]
    try:
        import json as _json
        chunks.append(_json.dumps(getattr(message, "msg_elements", None) or [],
                                  ensure_ascii=False))
    except Exception:
        pass
    for att in (getattr(message, "attachments", None) or []):
        chunks.append(str(att))
    for el in _walk(getattr(message, "msg_elements", None)):
        if el.get("content"):
            chunks.append(str(el.get("content")))
    card = getattr(message, "card_url", None)     # webhook 已从卡片 meta 挖出的跳转链接
    if card:
        chunks.insert(0, card)

    out, seen = [], set()
    for c in chunks:
        for m in _URL_RE.finditer(c or ""):
            u = m.group(0).rstrip(_TRIM)
            try:
                p = urlparse(u)
            except Exception:
                continue
            if p.scheme not in ("http", "https") or not p.hostname:
                continue
            if _host_skipped(p.hostname):
                continue
            if u.lower().split("?")[0].endswith(_SKIP_EXT):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
            if len(out) >= 3:
                return out
    return out


async def is_public_url(url):
    """SSRF 防护：只允许公网 http(s)，拒绝回环/内网/链路本地/保留地址。"""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (socket.gaierror, OSError, asyncio.TimeoutError):
        return False
    for info in infos or []:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _html_to_text(html):
    """极简正文提取：去噪 + 取 p/li/标题，返回 (标题, 正文)。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas",
                     "nav", "footer", "header", "aside", "form", "button"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main")
    if root is None:
        best, best_len = None, 0
        for div in soup.find_all(["div", "section"]):
            n = sum(len(p.get_text(strip=True)) for p in div.find_all("p"))
            if n > best_len:
                best, best_len = div, n
        root = best or soup.body or soup
    parts = []
    for el in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "td"]):
        t = el.get_text(" ", strip=True)
        if t and len(t) > 1:
            parts.append(t)
    if not parts:
        parts = [root.get_text(" ", strip=True)]
    text = "\n".join(parts)
    text = re.sub(r"[ \t\u00a0\u200b]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not title:
        h1 = root.find(["h1", "h2"])
        title = h1.get_text(" ", strip=True) if h1 else ""
    return title.strip(), text.strip()


async def _get(url, timeout):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.get(url, headers={"User-Agent": _UA,
                                       "Accept-Language": "zh-CN,zh;q=0.9,*;q=0.5"}) as r:
            if r.status not in (200, 203):
                raise RuntimeError("HTTP %s" % r.status)
            ctype = (r.headers.get("Content-Type") or "").lower()
            raw = await r.read()
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:2048], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore")
    return raw.decode(enc, "replace"), ctype


async def _jina(url, timeout):
    """JS 渲染页兜底：r.jina.ai 把页面转成 markdown（免费、无需 key，20 请求/分钟）。"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.get("https://r.jina.ai/" + url,
                         headers={"User-Agent": _UA}) as r:
            if r.status != 200:
                raise RuntimeError("jina HTTP %s" % r.status)
            return await r.text()


async def fetch_article(url):
    """抓取并抽正文，返回 (标题, 正文)。失败抛 RuntimeError。"""
    to = int(LINK_SUMMARY_TIMEOUT or 20)
    title, text = "", ""
    try:
        html, ctype = await _get(url, to)
        if "html" in ctype or "<html" in html[:2000].lower():
            title, text = _html_to_text(html)
        else:                     # pdf/纯文本等：直接用原文（截断）
            text = html
    except Exception as e:
        _log.info("[webtools] 直接抓取失败 %s: %s", url, e)

    if len(text) < 200 and LINK_SUMMARY_JINA_FALLBACK:
        try:
            md = await _jina(url, to + 15)
            if len(md) > len(text):
                text = re.sub(r"\n{3,}", "\n\n", md)
                if not title:
                    m = re.search(r"^Title:\s*(.+)$", md, re.M)
                    if m:
                        title = m.group(1).strip()
        except Exception as e:
            _log.info("[webtools] jina 兜底失败 %s: %s", url, e)

    text = text.strip()
    if len(text) < 80:
        raise RuntimeError("这个页面抓不到正文喵（可能是需登录/纯 JS 渲染的页面）")
    if not title:
        title = urlparse(url).netloc
    cap = int(LINK_SUMMARY_MAX_CHARS or 6000)
    return title[:80], text[:cap]


async def summarize(url):
    """抓正文 + 调 AI 出摘要，返回可直接发群的文本。"""
    title, body = await fetch_article(url)
    from bot.ai import ai as ai_mod
    user = "网页标题：%s\n网页地址：%s\n\n正文：\n%s" % (title, url, body)
    out = await ai_mod.complete_once(_SUMMARY_SYS, user, timeout=90)
    out = (out or "").strip()
    if not out:
        raise RuntimeError("AI 没生成摘要喵")
    if "🔗" not in out:
        out += "\n\n🔗 " + url
    return out[:1500]
