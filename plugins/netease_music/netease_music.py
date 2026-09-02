# -*- coding: utf-8 -*-
"""网易云点歌：按歌名搜索 → 候选列表 → 选号 → 下载 → 发送（file_type=3 语音）。

移植自 astrbot_plugin_netease_music，去掉对外部 Netease-CDN-Bypass 服务的依赖，
改用网易云官方接口（搜索 / 歌曲详情 / 播放地址）+ 免费外链兜底：
  · 搜索      music.163.com/api/search/get/web
  · 播放地址  /api/song/enhance/player/url（支持可选 NETEASE_COOKIE 解锁部分 VIP）
  · 兜底外链  music.163.com/song/media/outer/url?id={id}
免费歌曲两条链路必中其一；VIP/无版权曲返回 404，提示换一首。

命令：
  点歌 <歌名>   搜索并列出候选，回复数字点播
  （自然说法：来一首 X / 听歌 X / 网易点歌 X）
"""

import logging
import os
import re
import time
import urllib.parse
import uuid

import aiohttp

from config import ROOT
from bot.core.sender import FT_AUDIO, FT_IMAGE
from bot.commands import register, ROLE_ALL

_log = logging.getLogger("netease_music")

_TMP = os.path.join(ROOT, "tmp", "netease_music")
os.makedirs(_TMP, exist_ok=True)

_SEARCH_LIMIT = 5          # 候选数量
_SESSION_TIMEOUT = 60      # 选号窗口（秒）

_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://music.163.com",
    "Accept-Encoding": "gzip, deflate",
}

# 点歌会话：session_key -> {"songs": [...], "expire": ts}
SESSIONS = {}


def _net_cookie() -> str:
    """可选 NETEASE_COOKIE（来自 settings.json），用于解锁部分 VIP/版权受限歌曲。"""
    try:
        from config import _cfg
        return str(_cfg("NETEASE_COOKIE", "")) or ""
    except Exception:
        return ""


def _session_key(ctx):
    return (ctx.scene, ctx.target, ctx.openid)


def _normalize(text: str) -> str:
    return re.sub(r"^[/／!\s]+", "", text or "").strip()


def _trigger_kws():
    return ("点歌", "网易点歌", "听歌", "来一首", "唱一首", "music", "music ")


def _song_matcher(t):
    """消息以点歌类触发词开头时命中，并把后面的内容当作搜索词。"""
    t = _normalize(t)
    if not t:
        return False
    low = t.lower()
    for kw in _trigger_kws():
        if low == kw.lower():
            return True
        if low.startswith(kw.lower() + " ") or low.startswith(kw.lower() + "　"):
            return True
    return False


def _extract_keyword(ctx) -> str:
    t = _normalize(getattr(ctx.message, "content", None) or "")
    low = t.lower()
    for kw in _trigger_kws():
        if low == kw.lower():
            return ""
        if low.startswith(kw.lower() + " ") or low.startswith(kw.lower() + "　"):
            return t[len(kw):].strip()
    return ""


# ---------- 网易云官方接口 ----------
async def _get(url, cookie=""):
    headers = dict(_HDRS)
    if cookie:
        headers["cookie"] = cookie
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20), ssl=False) as r:
            return await r.json(content_type=None)


async def search_songs(keyword: str):
    """按歌名搜歌，返回规范化后的候选列表。"""
    url = ("https://music.163.com/api/search/get/web"
           f"?s={urllib.parse.quote(keyword)}&type=1&limit={_SEARCH_LIMIT}")
    try:
        data = await _get(url)
    except Exception as e:
        _log.warning("search failed: %s", e)
        return None
    raw = (data.get("result") or {}).get("songs") or []
    songs = []
    for so in raw:
        songs.append({
            "id": so.get("id"),
            "name": so.get("name", "未知歌曲"),
            "artists": "/".join(a.get("name", "") for a in (so.get("artists") or []) if isinstance(a, dict)),
            "album": (so.get("album") or {}).get("name", ""),
            "duration": so.get("duration") or 0,
        })
    return songs


async def get_song_detail(song_id: int):
    """取歌曲完整信息（标题/歌手/专辑/封面/时长）。"""
    url = f"https://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
    try:
        data = await _get(url)
    except Exception:
        return None
    songs = data.get("songs") or []
    return songs[0] if songs else None


async def _stream_to_file(url, path, cookie=""):
    """流式下载音频到本地文件，成功返回 True。"""
    headers = dict(_HDRS)
    if cookie:
        headers["cookie"] = cookie
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=60), ssl=False,
                             allow_redirects=True) as r:
                if r.status != 200:
                    return False
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "json" in ctype or "text/html" in ctype or "text/plain" in ctype:
                    return False
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(64 * 1024):
                        f.write(chunk)
    except Exception as e:
        _log.warning("download failed: %s", e)
        return False
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size < 10240:  # <10KB 基本是错误页/残片
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    return True


async def download_audio(song_id: int) -> str | None:
    """下载歌曲 mp3 到本地临时文件。链路：enhance player → outer 外链 → None。"""
    cookie = _net_cookie()
    path = os.path.join(_TMP, uuid.uuid4().hex + ".mp3")

    # 1. 官方播放地址（可选 cookie 解锁部分 VIP）
    play = None
    try:
        data = await _get(
            f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=128000",
            cookie=cookie,
        )
        play = (data.get("data") or [{}])[0].get("url")
    except Exception:
        play = None
    if play and await _stream_to_file(play, path, cookie):
        return path

    # 2. 免费外链兜底（非 VIP 曲可用）
    outer = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
    if await _stream_to_file(outer, path, cookie):
        return path

    try:
        os.remove(path)
    except OSError:
        pass
    return None


async def _download_cover(url: str) -> str | None:
    """下载封面到本地临时文件，失败返回 None。"""
    if not url:
        return None
    path = os.path.join(_TMP, uuid.uuid4().hex + ".jpg")
    try:
        async with aiohttp.ClientSession(headers=_HDRS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as r:
                if r.status != 200:
                    return None
                with open(path, "wb") as f:
                    f.write(await r.read())
    except Exception:
        return None
    return path if os.path.getsize(path) > 0 else None


# ---------- 命令 ----------
@register(keywords=["点歌"], help="网易云点歌（点歌 歌名）喵", matcher=_song_matcher, role=ROLE_ALL)
async def cmd_netease_music(ctx):
    keyword = _extract_keyword(ctx)
    if not keyword:
        await ctx.reply("想听什么歌呀？可以这样：点歌 Lemon / 听歌 稻香 喵~")
        return

    await ctx.reply("正在为你找歌喵，稍等~")
    songs = await search_songs(keyword)
    if songs is None:
        await ctx.reply("和网易云的连接断掉了喵，请稍后再试")
        return
    if not songs:
        await ctx.reply(f"没找到「{keyword}」这首歌喵…换个关键词试试？")
        return

    SESSIONS[_session_key(ctx)] = {
        "songs": songs,
        "expire": time.time() + _SESSION_TIMEOUT,
    }
    lines = [f"为你找到 {len(songs)} 首歌喵！回复数字点播~"]
    for i, so in enumerate(songs, 1):
        dur = f"{so['duration'] // 60000}:{(so['duration'] % 60000) // 1000:02d}" if so["duration"] else "?"
        artists = so["artists"] or "未知歌手"
        album = f"《{so['album']}》" if so["album"] else ""
        lines.append(f"{i}. {so['name']} - {artists} {album} [{dur}]")
    lines.append("回复「0」取消选择喵")
    await ctx.reply_text("\n".join(lines), reply=False)


async def _play(ctx, songs, num: int):
    selected = songs[num - 1]
    song_id = selected["id"]
    song_id = int(song_id)

    detail = await get_song_detail(song_id)
    if detail:
        title = detail.get("name") or selected["name"]
        artists = "/".join(
            a.get("name", "") for a in (detail.get("artists") or []) if isinstance(a, dict)
        ) or selected["artists"] or "未知歌手"
        cover_url = (detail.get("album") or {}).get("picUrl", "") + "?param=300y300"
        duration = detail.get("duration") or selected["duration"]
    else:
        title, artists, cover_url = selected["name"], selected["artists"] or "未知歌手", ""
        duration = selected["duration"]
    dur = f"{duration // 60000}:{(duration % 60000) // 1000:02d}" if duration else "?"

    await ctx.reply_text(f"🎵 正在播放：{title}\n🎤 {artists}  ⏳ {dur} 喵~", reply=False)

    audio_path = await download_audio(song_id)
    if not audio_path:
        await ctx.reply(
            f"《{title}》暂时听不了喵…\n"
            "这首歌可能是 VIP / 无版权曲目。可在 settings.json 配置 netease_cookie 解锁部分 VIP 歌曲。"
        )
        return

    try:
        res = await ctx.sender.send_local_file(ctx.message, FT_AUDIO, audio_path, reply=False)
        if isinstance(res, str) and res.startswith("发送失败"):
            await ctx.reply(f"{res}（该群可能未开启发送权限）")
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    # 附带封面
    cover_path = await _download_cover(cover_url)
    if cover_path:
        try:
            await ctx.sender.send_local_file(ctx.message, FT_IMAGE, cover_path, reply=False)
        finally:
            try:
                os.remove(cover_path)
            except OSError:
                pass


# ---------- 选号拦截（dispatch 顶部调用）----------
async def consume(ctx):
    """点歌候选中，用户回复纯数字表示选号；「0」取消。"""
    s = SESSIONS.get(_session_key(ctx))
    if not s:
        return False
    if time.time() > s["expire"]:
        SESSIONS.pop(_session_key(ctx), None)
        return False

    raw = (getattr(ctx.message, "content", None) or "").strip()
    if not raw.isdigit():
        return False

    SESSIONS.pop(_session_key(ctx), None)
    num = int(raw)
    if num == 0:
        await ctx.reply("好的喵，这次不选了~")
        return True
    if not (1 <= num <= len(s["songs"])):
        await ctx.reply(f"喵？候选里只有 1~{len(s['songs'])} 首喵，重新点歌吧~")
        return True
    await _play(ctx, s["songs"], num)
    return True


# web 后台「其他功能」模块分组用（见 bot/core/webui.py 的 _module_groups）
NCM_CMD_NAMES = {"cmd_netease_music"}