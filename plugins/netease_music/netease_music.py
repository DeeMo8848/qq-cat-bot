# -*- coding: utf-8 -*-
"""网易云点歌：按歌名搜索 → 候选列表 → 选号 → 下载 → 发送。

发送策略（★ 重要，2026-09-22 实测确认）：
  「语音」(file_type=3) 在 QQ 平台有 **300 秒硬性时长上限**（实测 300s 通过、
  301s 报 40093013「上传音频时长超过限制」）。两条上传通道（分片 / URL）都在
  服务端同一处校验，客户端无法绕过——这是平台限制，不是配置项。
  因此：≤300s 用语音发（客户端可直接播放，体验最好）；
        >300s **直接回绝并说明原因**（不降级为文件发送，按用户要求）。
  若日后腾讯放宽此限制，把 _AUDIO_MAX_SEC 调大即可恢复自动放行。

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

# ★ QQ 语音(file_type=3)的硬性时长上限（秒）。实测 300 通过、301 报 40093013。
#   超过上限的曲子直接回绝（不降级为文件发送）。
_AUDIO_MAX_SEC = 300

# ★ 点歌请求的音频码率（bps）。固定 128k：
#   · QQ 语音会再次转码，320k 对最终听感几乎没有增益
#   · 128k 单曲约 3~4MB，比 320k（10MB+）下载/上传快得多
#   可在 settings.json 用 NETEASE_BR 覆盖（如 192000）。
_AUDIO_BR = 128000

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


def _audio_br() -> int:
    """点歌下载码率（bps）。settings.json 的 NETEASE_BR 可覆盖，非法值回退默认。"""
    try:
        from config import _cfg
        v = int(_cfg("NETEASE_BR", _AUDIO_BR))
        return v if v >= 64000 else _AUDIO_BR
    except Exception:
        return _AUDIO_BR


def _fmt_dur(ms) -> str:
    """毫秒 → 'm:ss'。"""
    try:
        ms = int(ms or 0)
    except Exception:
        return "?"
    if ms <= 0:
        return "?"
    return f"{ms // 60000}:{(ms % 60000) // 1000:02d}"


def _session_key(ctx):
    return (ctx.scene, ctx.target, ctx.openid)


def _normalize(text: str) -> str:
    return re.sub(r"^[/／!\s]+", "", text or "").strip()


def _trigger_kws():
    return ("点歌", "网易点歌", "听歌", "来一首", "唱一首", "music", "music ")


def _song_matcher(t):
    """消息以点歌类触发词开头时命中，并把后面的内容当作搜索词。

    允许「点歌 稻香」「点歌稻香」「点歌　稻香」以及「点歌:稻香」等写法，
    只要能抠出非空歌名即命中。单独一个触发词（无歌名）也命中，
    由 cmd_netease_music 回提示语。
    """
    kw = _match_trigger(t)
    return kw is not None


_SEP_CHARS = " \u3000\t:：,，-—、.。/／!！?？"


def _match_trigger(text):
    """若消息以某个触发词开头，返回该触发词；否则返回 None。

    规则：
      · 整条消息就等于触发词          → 命中（提示用户补歌名）
      · 触发词 + 分隔符 + 任意内容    → 命中
      · 中文触发词直接接中文/字母数字 → 命中（支持「点歌稻香」「来一首Lemon」）
      · 英文触发词（music）必须带分隔符，避免「musicabc」误触发
    """
    t = _normalize(text)
    if not t:
        return None
    low = t.lower()
    for kw in _trigger_kws():
        kl = kw.lower()
        if not low.startswith(kl):
            continue          # ← 必须以触发词开头，否则跳过
        rest = t[len(kw):]
        if not rest:
            return kw         # 只有触发词本身
        if rest[0] in _SEP_CHARS:
            return kw
        # 中文触发词允许直接接歌名（「点歌稻香」「来一首Lemon」）
        if not kw[-1].isascii() and rest[0].isalnum():
            return kw
    return None


def _extract_keyword(ctx) -> str:
    t = _normalize(getattr(ctx.message, "content", None) or "")
    kw = _match_trigger(t)
    if kw is None:
        return ""
    rest = t[len(kw):].strip()
    return rest.lstrip(_SEP_CHARS).strip()


# ---------- 网易云官方接口 ----------
async def _get(url, cookie=""):
    """GET 一个 JSON 接口。返回解析后的对象；非 JSON / 非 2xx 返回 None。

    注意：网易云部分老接口返回的 Content-Type 是 text/plain，且 result 字段可能
    被服务端加密成字符串，所以这里绝不假设返回值一定是 dict。
    """
    headers = dict(_HDRS)
    if cookie:
        headers["cookie"] = cookie
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20),
                             ssl=False) as r:
                if r.status != 200:
                    _log.warning("http %s for %s", r.status, url)
                    return None
                return await r.json(content_type=None)
    except Exception as e:
        _log.warning("request failed %s: %s", url, e)
        return None


def _songs_of(data):
    """从搜索响应里稳净地抠出 songs 列表。

    兼容三种形态：
      · cloudsearch/pc : {"result": {"songs": [...]}}
      · search/get     : {"result": {"songs": [...]}}
      · 加密响应        : {"result": "<加密字符串>"}  → 视为无结果
    """
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict):
        songs = result.get("songs")
        if isinstance(songs, list):
            return songs
        return []
    # result 是字符串（被加密）或缺失 → 无可用结果
    if isinstance(result, str):
        _log.warning("search result looks encrypted (str len=%d)", len(result))
    return []


# 搜索接口：cloudsearch/pc 返回明文 JSON，作为首选；旧的 search/get/web 已把
# result 加密成字符串，不再能用，仅作最后兜底。
_SEARCH_APIS = (
    "https://music.163.com/api/cloudsearch/pc?s={q}&type=1&limit={n}&offset=0",
    "https://music.163.com/api/search/get/web?s={q}&type=1&limit={n}",
)


async def search_songs(keyword: str):
    """按歌名搜歌，返回规范化后的候选列表；彻底失败返回 None。"""
    raw = []
    for tpl in _SEARCH_APIS:
        url = tpl.format(q=urllib.parse.quote(keyword), n=_SEARCH_LIMIT)
        data = await _get(url)
        songs = _songs_of(data)
        if songs:
            raw = songs
            break
    if not raw:
        return []

    out = []
    for so in raw:
        if not isinstance(so, dict):
            continue
        # cloudsearch 的 artists 是 list[dict]；个别接口给字符串
        artists = so.get("artists") or so.get("ar") or []
        if isinstance(artists, list):
            names = "/".join(a.get("name", "") for a in artists
                             if isinstance(a, dict) and a.get("name"))
        else:
            names = str(artists)
        album = so.get("album") or so.get("al") or {}
        album_name = album.get("name", "") if isinstance(album, dict) else ""
        out.append({
            "id": so.get("id"),
            "name": so.get("name", "未知歌曲"),
            "artists": names,
            "album": album_name,
            "duration": so.get("duration") or so.get("dt") or 0,
        })
    return out


async def get_song_detail(song_id: int):
    """取歌曲完整信息（标题/歌手/专辑/封面/时长）。"""
    url = f"https://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
    data = await _get(url)
    if not isinstance(data, dict):
        return None
    songs = data.get("songs") or []
    if not isinstance(songs, list) or not songs:
        return None
    first = songs[0]
    return first if isinstance(first, dict) else None


async def _stream_to_file(url, path, cookie=""):
    """流式下载音频到本地文件，成功返回 True。

    超时策略：用 sock_read（单次读超时）而不是 total。
    ★ 实测教训：`total=60` 会在高码率大文件（如无损/320k、5MB+）上误杀——
    请求本身是健康的，只是整体耗时超过 60s。真正该防的是「连接建立后卡住不动」，
    那用 sock_read 判断即可，总时长不该设上限。
    """
    headers = dict(_HDRS)
    if cookie:
        headers["cookie"] = cookie
    # connect 10s；单次读 30s 无数据才算异常；不给 total 上限
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.get(url, timeout=timeout, ssl=False,
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
    """下载歌曲 mp3 到本地临时文件。链路：enhance player → outer 外链 → None。

    ★ 文件名一律用随机 hex（不要用歌名）：服务器是 Linux，歌名里的中文/空格/斜杠
    会让落盘路径出问题（实测传「起风了 - test」时 _stream_to_file 直接失败）。
    语音发送不显示文件名，所以随机名没有任何副作用。
    """
    cookie = _net_cookie()
    path = os.path.join(_TMP, uuid.uuid4().hex + ".mp3")

    # 1. 官方播放地址（可选 cookie 解锁部分 VIP）
    # ★ 固定 128k：QQ 语音会再转码，高码率对音质几乎没有增益，却让下载/上传明显变慢
    #   （用户反馈「等太久以为插件又挂了」）。128k 单曲约 3~4MB，传输快很多。
    br = _audio_br()
    play = None
    data = await _get(
        f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br={br}",
        cookie=cookie,
    )
    if isinstance(data, dict):
        arr = data.get("data")
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            play = arr[0].get("url")
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
    try:
        await _cmd_netease_music(ctx)
    except Exception as e:  # 任何意外都要让用户看到反馈，而不是「点了没反应」
        _log.exception("netease cmd failed: %s", e)
        try:
            await ctx.reply("点歌出错喵…稍后再试试？")
        except Exception:
            pass


async def _cmd_netease_music(ctx):
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
    dur = _fmt_dur(duration)

    # ★ 时长预检：超过 QQ 语音 300s 上限的直接回绝，不下载、不尝试发送。
    #   实测该限制在平台服务端（两条上传通道都会拒），客户端绕不过去。
    sec = (duration or 0) / 1000.0
    if sec > _AUDIO_MAX_SEC:
        await ctx.reply("发送失败喵：音频时长超过限制了喵")
        return

    await ctx.reply_text(f"🎵 正在播放：{title}\n🎤 {artists}  ⏳ {dur} 喵~", reply=False)

    # ★ 文件名一律随机 hex：Linux 上中文/空格名会导致下载落盘失败，
    #   且语音发送本就不显示文件名，随机名无副作用。
    audio_path = await download_audio(song_id)
    if not audio_path:
        # ★ 区分「没配 cookie」和「配了也拿不到」：前者可引导用户去配置，后者是真无版权
        if _net_cookie():
            await ctx.reply(
                f"《{title}》暂时听不了喵…\n"
                "这首歌可能没有版权或需要会员才能播放。"
            )
        else:
            await ctx.reply(
                f"《{title}》暂时听不了喵…\n"
                "这首歌可能是 VIP / 无版权曲目。可在 settings.json 配置 netease_cookie 解锁部分 VIP 歌曲。"
            )
        return

    try:
        # ★ 始终用「语音」(file_type=3) 发送：客户端可直接播放，体验最好。
        #   不按 300s 自动降级为文件——超时长的已在上面回绝掉了。
        res = await ctx.sender.send_local_file(
            ctx.message, FT_AUDIO, audio_path, reply=False)

        if isinstance(res, str) and res.startswith("发送失败"):
            # 区分「时长超限」与其他失败：前者是平台规则，别说成「群没开权限」误导用户
            if "时长" in res or "40093013" in res:
                await ctx.reply(
                    f"《{title}》语音发送被 QQ 拒了喵（{dur} 超过语音时长上限）。",
                )
            else:
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
    try:
        await _play(ctx, s["songs"], num)
    except Exception as e:
        _log.exception("play failed: %s", e)
        try:
            await ctx.reply("这首歌播放失败了喵…换一首试试？")
        except Exception:
            pass
    return True


# web 后台「其他功能」模块分组用（见 bot/core/webui.py 的 _module_groups）
NCM_CMD_NAMES = {"cmd_netease_music"}