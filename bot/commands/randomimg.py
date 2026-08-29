# -*- coding: utf-8 -*-
"""「随机一图」命令模块：多个随机图片 API + 本地龙图 + 猪猪图库。

图源分三类：
  · 直出图 API：GET 直接返回图片（UAPI/樱花/栗次元/兽耳酱/天逸/小姐姐/南风系列）
  · JSON API：返回 JSON 里解析出图片地址（Pixiv Yuki / Lolicon / pighub 猪猪）
  · 本地目录：从本地文件夹随机挑一张（龙图）

命令：
  随机一图                    全部可用图源随机挑一个发图
  UAPI随机图/樱花随机图/栗次元随机图/随机兽耳酱/天逸随机图/我要小姐姐
  南风随机图[pc/pe/tx]  南风随机图风景[pc/pe]
  yuki 或 pid{ID}            Pixiv Yuki（不分大小写；pid 按作品 ID 搜）
  Lolicon随机图               Lolicon 随机（r18=0）
  Lolicon [标签...] [数量]     Lolicon 按标签搜索（自动过滤 NSFW 标签）
  随机龙                      本地龙图随机一张
  抽猪                        pighub 猪猪图库随机一张（带名字）
"""

import json
import logging
import os
import random
import re
import uuid

import aiohttp

from config import ROOT, DRAGON_DIR
from . import register, ROLE_ALL

_log = logging.getLogger("randomimg")

_TMP_DIR = os.path.join(ROOT, "tmp", "randomimg")
os.makedirs(_TMP_DIR, exist_ok=True)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# 本地龙图目录（机器相关，来自 settings.json，不提交）

# 随机奶龙：从 GitHub 仓库 nailong-memes 的 gif/images 目录随机取一张
NAILONG_REPO = "GGGeeeooorrrgggeee/nailong-memes"
NAILONG_API_TMPL = "https://api.github.com/repos/{}/contents/{}"
NAILONG_RAW_TMPL = "https://raw.githubusercontent.com/{}/main/{}"
NAILONG_DIRS = ("gif", "images")

# 直接返回图片的 API（关键词, 展示名, URL）
DIRECT_APIS = [
    ("UAPI随机图",       "UAPI",     "https://uapis.cn/api/v1/random/image"),
    ("樱花随机图",       "樱花",     "https://www.dmoe.cc/random.php"),
    ("栗次元随机图",     "栗次元",   "https://t.alcy.cc/moez"),
    ("随机兽耳酱",       "兽耳酱",   "https://t.alcy.cc/xhl"),
    ("天逸随机图",       "天逸",     "https://api.mtyqx.cn/api/random.php"),
    ("我要小姐姐",       "小姐姐",   "https://api.mtyqx.cn/xjjapi/random.php"),
    ("南风随机图",       "南风·动漫",        "https://api.sretna.cn/api/anime/auto"),
    ("南风随机图pc",     "南风·动漫PC",      "https://api.sretna.cn/api/anime/pc"),
    ("南风随机图pe",     "南风·动漫PE",      "https://api.sretna.cn/api/anime/pe"),
    ("南风随机图tx",     "南风·动漫头像",    "https://api.sretna.cn/api/anime/tx"),
    ("南风随机图风景",   "南风·风景",        "https://api.sretna.cn/api/scenery/auto"),
    ("南风随机图风景pc", "南风·风景PC",      "https://api.sretna.cn/api/scenery/pc"),
    ("南风随机图风景pe", "南风·风景PE",      "https://api.sretna.cn/api/scenery/pe"),
]

# 后台「随机图片」模块包含的命令函数名（用于在 Web 后台归为一个模块展示）
RANDOMIMG_CMD_NAMES = (
    {"cmd_random_all", "cmd_random_menu", "cmd_yuki", "cmd_lolicon", "cmd_dragon", "cmd_pighub", "cmd_nailong"}
    | {"cmd_" + kw for kw, _, _ in DIRECT_APIS}
)

# Lolicon 搜索时忽略的标签（防止误触发 r18 导致封号/内容审核拦截风险）
NSFW_TAGS = {
    "r18", "r-18", "r18g", "r-18g", "nsfw", "裸体", "全裸", "裸露", "露出", "成人",
    "色情", "性交", "性爱", "性器", "乳首", "乳房", "触手", "猎奇", "血腥", "断肢",
    "肢解", "内脏", "尸体", "腐烂", "虐杀", "guro", "gore", "grotesque", "グロ",
    "グロテスク", "リョナ", "猟奇", "欠損", "切断", "内臓", "死体",
}

# 擦边标签：不一定是 R-18，但容易触发 QQ 内容审核(40034006)的图，同样丢弃
RACY_TAGS = {
    # 泳装 / 内衣
    "泳装", "水着", "swimwear", "swimsuit", "比基尼", "ビキニ", "bikini",
    "マイクロビキニ", "縞ビキニ", "黒ビキニ", "内衣", "下着", "underwear",
    "内裤", "胖次", "ぱんつ", "パンツ", "panties", "パンチラ", "露内裤", "panty shot",
    # 身体部位
    "乳沟", "谷間", "魅惑の谷間", "cleavage", "巨乳", "爆乳", "large breasts",
    "huge breasts", "欧派", "おっぱい", "breasts", "boobs", "大腿", "ふともも",
    "魅惑のふともも", "thighs",
    # 丝袜 / 网袜
    "丝袜", "黑丝", "网袜", "タイツ", "網タイツ", "黒スト", "stockings",
    "fishnets", "thighhighs",
    # 兔女郎 / 暗示系
    "兔女郎", "バニーガール", "bunny girl", "bunnygirl", "淫纹", "淫紋",
    "拘束", "束缚", "bondage",
}
_BLOCK_LOWER = {x.lower() for x in NSFW_TAGS | RACY_TAGS}

API_DEAD = "api死了喵"

# Yuki 的图片 CDN（Vercel）校验浏览器头，缺了会 403 Forbidden，需带完整浏览器头
_BROWSER_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://pixiv.yuki.sh/",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp",
}


async def _fetch_bytes(url, headers=None):
    """GET 下载字节，返回 (bytes, content_type)。失败返回 (None, '')。"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=_TIMEOUT, ssl=False, headers=headers) as r:
                if r.status != 200:
                    return None, ""
                return await r.read(), r.headers.get("Content-Type", "")
    except Exception:
        return None, ""


async def _send_image_bytes(ctx, data, ctype=""):
    """把图片字节保存到临时文件并发送，发完清理。返回是否发送成功。"""
    if not data:
        await ctx.reply(API_DEAD)
        return False
    ext = _EXT_BY_TYPE.get((ctype or "").lower(), ".jpg")
    path = os.path.join(_TMP_DIR, uuid.uuid4().hex + ext)
    try:
        with open(path, "wb") as f:
            f.write(data)
        try:
            res = await ctx.sender.send_local_file(ctx.message, 1, path, reply=False)
            return not (isinstance(res, str) and res.startswith("发送失败"))
        except Exception:
            return False
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ---------- 直出图 API ----------
async def _send_direct(ctx, url):
    data, ctype = await _fetch_bytes(url)
    await _send_image_bytes(ctx, data, ctype)


# ---------- Pixiv Yuki ----------
YUKI_RECOMMEND = "https://pixiv.yuki.sh/api/recommend?type=json"
YUKI_ILLUST = "https://pixiv.yuki.sh/api/illust?id={}"


def _yuki_matcher(t):
    t = (t or "").strip()
    if not t:
        return False
    return t.lower().startswith("yuki") or bool(re.match(r"^pid\s*\d+", t, re.I))


async def _send_yuki(ctx, pid):
    url = YUKI_ILLUST.format(pid) if pid else YUKI_RECOMMEND
    data, _ = await _fetch_bytes(url, headers=_BROWSER_HDRS)
    if not data:
        await ctx.reply(API_DEAD)
        return
    try:
        j = json.loads(data)
    except Exception:
        await ctx.reply(API_DEAD)
        return
    if not j.get("success") or not j.get("data"):
        await ctx.reply("图可能被猫吃了喵")
        return
    urls = (j.get("data") or {}).get("urls") or {}
    img_url = urls.get("regular") or urls.get("original") or urls.get("small")
    if not img_url:
        await ctx.reply("图可能被猫吃了喵")
        return
    img, ctype = await _fetch_bytes(img_url, headers=_BROWSER_HDRS)
    await _send_image_bytes(ctx, img, ctype)


# ---------- Lolicon ----------
LOLICON_API = "https://api.lolicon.app/setu/v2"


def _lolicon_matcher(t):
    t = (t or "").strip()
    return t.lower().startswith("lolicon")


def _is_nsfw_item(it):
    """判断 Lolicon 返回的这条图是否带 NSFW 标签。

    即使请求里带了 r18=0，API 仍可能混进带 R-18 等标签的图，
    直接发送会被 QQ 内容审核拦截(40034006)，必须在这里二次过滤。
    """
    return any(str(t).lower() in _BLOCK_LOWER for t in (it.get("tags") or []))


async def _send_lolicon(ctx, tags, num=1):
    # 向后端多要几张，过滤掉带 NSFW/擦边标签的图后仍能凑够 num 张
    req_num = min(num + 10, 20)
    payload = {"r18": 0, "num": req_num, "size": ["original"]}
    if tags:
        payload["tag"] = tags
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(LOLICON_API, json=payload, timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    await ctx.reply(API_DEAD)
                    return
                j = await r.json(content_type=None)
    except Exception:
        await ctx.reply(API_DEAD)
        return
    items = j.get("data") or []
    # 二次过滤 NSFW：带 R-18 等标签的图直接丢弃，避免 QQ 内容审核拦截(40034006)
    clean = [it for it in items if not _is_nsfw_item(it)]
    if not clean:
        await ctx.reply("没找到相关图片喵")
        return
    sent = 0
    for it in clean:
        if sent >= num:
            break
        u = (it.get("urls") or {}).get("original")
        if not u:
            continue
        img, ctype = await _fetch_bytes(u)
        if not img:
            continue
        if await _send_image_bytes(ctx, img, ctype):
            sent += 1
    if sent == 0:
        await ctx.reply("没找到相关图片喵")


# ---------- pighub 猪猪图库 ----------
PIGHUB_API = "https://pighub.top/api/images?sort=3"
PIGHUB_BASE = "https://pighub.top"


async def _send_pighub(ctx):
    data, _ = await _fetch_bytes(PIGHUB_API)
    if not data:
        await ctx.reply(API_DEAD)
        return
    try:
        j = json.loads(data)
    except Exception:
        await ctx.reply(API_DEAD)
        return
    items = j.get("data") or []
    if not items:
        await ctx.reply(API_DEAD)
        return
    item = random.choice(items)
    rel = item.get("image_url") or ""
    title = item.get("title") or ""
    if not rel:
        await ctx.reply(API_DEAD)
        return
    img, ctype = await _fetch_bytes(PIGHUB_BASE + rel)
    if not img:
        await ctx.reply(API_DEAD)
        return
    ext = _EXT_BY_TYPE.get((ctype or "").lower(), ".jpg")
    path = os.path.join(_TMP_DIR, uuid.uuid4().hex + ext)
    try:
        with open(path, "wb") as f:
            f.write(img)
        if title:
            await ctx.sender.send_image_with_text(ctx.message, f"你抽到了【{title}】喵", path, reply=False)
        else:
            await ctx.sender.send_local_file(ctx.message, 1, path, reply=False)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ---------- 本地龙图 ----------
async def _send_dragon(ctx):
    try:
        files = [f for f in os.listdir(DRAGON_DIR)
                 if os.path.isfile(os.path.join(DRAGON_DIR, f))]
    except Exception:
        await ctx.reply("龙图目录不见了喵")
        return
    if not files:
        await ctx.reply("龙图目录空了喵")
        return
    path = os.path.join(DRAGON_DIR, random.choice(files))
    try:
        await ctx.sender.send_local_file(ctx.message, 1, path, reply=False)
    except Exception as e:
        await ctx.reply(f"发送龙图失败喵：{e}")


# ---------- 随机奶龙（GitHub 仓库图片） ----------
async def _fetch_nailong_items():
    """拉取 nailong-memes 仓库 gif/images 目录的文件列表，返回 [("文件名", "下载直链")]。"""
    items = []
    for sub in NAILONG_DIRS:
        url = NAILONG_API_TMPL.format(NAILONG_REPO, sub)
        data, _ = await _fetch_bytes(url, headers={"User-Agent": "curl"})
        if not data:
            continue
        try:
            j = json.loads(data)
        except Exception:
            continue
        for it in j:
            name = (it.get("name") or "").strip()
            if name and isinstance(it.get("download_url"), str):
                items.append((name, it["download_url"]))
    return items


async def _send_nailong(ctx):
    items = await _fetch_nailong_items()
    if not items:
        await ctx.reply("奶龙图库空了喵")
        return
    _, url = random.choice(items)
    img, ctype = await _fetch_bytes(url, headers={"User-Agent": "curl"})
    await _send_image_bytes(ctx, img, ctype)


# ---------- 网页预览代理：供 Web 后台 /api/randomimg/preview 调用 ----------
PREVIEW_SOURCES = {
    "uapi": "https://uapis.cn/api/v1/random/image",
    "sakura": "https://www.dmoe.cc/random.php",
    "lcy": "https://t.alcy.cc/moez",
    "xhl": "https://t.alcy.cc/xhl",
    "tianyi": "https://api.mtyqx.cn/api/random.php",
    "xj": "https://api.mtyqx.cn/xjjapi/random.php",
    "nf_anime_auto": "https://api.sretna.cn/api/anime/auto",
    "nf_anime_pc": "https://api.sretna.cn/api/anime/pc",
    "nf_anime_pe": "https://api.sretna.cn/api/anime/pe",
    "nf_anime_tx": "https://api.sretna.cn/api/anime/tx",
    "nf_scenery_auto": "https://api.sretna.cn/api/scenery/auto",
    "nf_scenery_pc": "https://api.sretna.cn/api/scenery/pc",
    "nf_scenery_pe": "https://api.sretna.cn/api/scenery/pe",
}


async def fetch_preview_image(source, **kw):
    """按 source 取一张预览图，返回 (bytes, content_type)。失败返回 (None, '')。

    可选参数（来自网页端输入框）：
      · yuki:    pid  指定作品ID（留空走随机推荐）
      · lolicon: tag  标签（空格/逗号分隔）、num 数量（默认1）
    """
    if source in PREVIEW_SOURCES:
        return await _fetch_bytes(PREVIEW_SOURCES[source])
    if source == "yuki":
        pid = (kw.get("pid") or "").strip()
        url = YUKI_ILLUST.format(pid) if pid else YUKI_RECOMMEND
        data, _ = await _fetch_bytes(url, headers=_BROWSER_HDRS)
        if not data:
            return None, ""
        try:
            j = json.loads(data)
        except Exception:
            return None, ""
        urls = (j.get("data") or {}).get("urls") or {}
        u = urls.get("regular") or urls.get("original") or urls.get("small")
        if not u:
            return None, ""
        return await _fetch_bytes(u, headers=_BROWSER_HDRS)
    if source == "lolicon":
        try:
            tag = (kw.get("tag") or "").strip()
            num = 1
            try:
                num = max(1, min(int(kw.get("num") or 1), 10))
            except Exception:
                num = 1
            payload = {"r18": 0, "num": num, "size": ["original"]}
            if tag:
                payload["tag"] = [t for t in re.split(r"[\s,，]+", tag) if t]
            async with aiohttp.ClientSession() as s:
                async with s.post(LOLICON_API, json=payload, timeout=_TIMEOUT, ssl=False) as r:
                    if r.status != 200:
                        return None, ""
                    j = await r.json(content_type=None)
        except Exception:
            return None, ""
        u = ((j.get("data") or [{}])[0].get("urls") or {}).get("original")
        if not u:
            return None, ""
        return await _fetch_bytes(u)
    if source == "pighub":
        data, _ = await _fetch_bytes(PIGHUB_API)
        if not data:
            return None, ""
        try:
            j = json.loads(data)
        except Exception:
            return None, ""
        items = j.get("data") or []
        if not items:
            return None, ""
        rel = random.choice(items).get("image_url")
        if not rel:
            return None, ""
        return await _fetch_bytes(PIGHUB_BASE + rel)
    if source == "dragon":
        try:
            files = [f for f in os.listdir(DRAGON_DIR)
                     if os.path.isfile(os.path.join(DRAGON_DIR, f))]
        except Exception:
            return None, ""
        if not files:
            return None, ""
        path = os.path.join(DRAGON_DIR, random.choice(files))
        with open(path, "rb") as f:
            import mimetypes
            return f.read(), mimetypes.guess_type(path)[0] or "image/jpeg"
    if source == "nailong":
        items = await _fetch_nailong_items()
        if not items:
            return None, ""
        _, url = random.choice(items)
        return await _fetch_bytes(url, headers={"User-Agent": "curl"})
    return None, ""


def _exact(kw):
    return lambda t: (t or "").strip() == kw


# 「随机图片」命令清单（精简版式，同主菜单风格）
RANDOMIMG_MENU = "\n".join([
    "🖼️ 随机图片",
    "· 随机一图",
    "· UAPI随机图",
    "· 樱花随机图",
    "· 栗次元随机图",
    "· 随机兽耳酱",
    "· 天逸随机图",
    "· 我要小姐姐",
    "· 南风随机图[pc/pe/tx]",
    "· 南风随机图风景[pc/pe]",
    "· yuki 或 pid{ID}",
    "· Lolicon [标签 数量]",
    "· 随机龙",
    "· 随机奶龙",
    "· 抽猪",
])


# ---------- 命令注册 ----------
@register(keywords=["随机图片"], help="随机图片命令清单喵", matcher=_exact("随机图片"), role=ROLE_ALL, exact=True)
async def cmd_random_menu(ctx):
    await ctx.reply(RANDOMIMG_MENU)


@register(keywords=["随机一图"], help="从所有随机图API随机发一张喵", matcher=_exact("随机一图"), role=ROLE_ALL, exact=True)
async def cmd_random_all(ctx):
    # 只从「后台没关掉」的图源里随机挑，避免随机抽到被禁用的图源（如 Lolicon）
    from bot.core import state
    sources = [("direct", kw) for kw, _, _ in DIRECT_APIS if state.is_enabled("cmd_" + kw)]
    for name in ("yuki", "lolicon", "pighub", "dragon", "nailong"):
        if state.is_enabled("cmd_" + name):
            sources.append((name, ""))
    if not sources:
        await ctx.reply("所有图源都被关掉了喵")
        return
    kind, kw = random.choice(sources)
    if kind == "direct":
        url = next(u for k, _, u in DIRECT_APIS if k == kw)
        await _send_direct(ctx, url)
    elif kind == "yuki":
        await _send_yuki(ctx, None)
    elif kind == "lolicon":
        await _send_lolicon(ctx, [], 1)
    elif kind == "pighub":
        await _send_pighub(ctx)
    elif kind == "nailong":
        await _send_nailong(ctx)
    else:
        await _send_dragon(ctx)


def _make_direct_cmd(kw, url):
    @register(keywords=[kw], help=f"{kw}喵", matcher=_exact(kw), role=ROLE_ALL, exact=True)
    async def _cmd(ctx, _url=url):
        await _send_direct(ctx, _url)
    _cmd.__name__ = "cmd_" + kw
    return _cmd


for _kw, _title, _url in DIRECT_APIS:
    _make_direct_cmd(_kw, _url)


@register(keywords=["yuki"], help="Pixiv Yuki随机图（yuki 或 pid作品ID）喵", matcher=_yuki_matcher, role=ROLE_ALL)
async def cmd_yuki(ctx):
    t = (ctx.args or "").strip()
    m = re.match(r"^pid\s*(\d+)", t, re.I)
    await _send_yuki(ctx, m.group(1) if m else None)


@register(keywords=["Lolicon"], help="Lolicon随机图（Lolicon 标签 数量）喵", matcher=_lolicon_matcher, role=ROLE_ALL)
async def cmd_lolicon(ctx):
    t = (ctx.args or "").strip()
    rest = re.sub(r"^lolicon\s*", "", t, flags=re.I).strip()
    # 允许「Lolicon搜索 X」「Lolicon搜 X」这类说法，搜索二字只是口头语，不能当标签传
    rest = re.sub(r"^(随机图|搜索|搜|查找|找)\s*", "", rest).strip()
    if not rest:
        await _send_lolicon(ctx, [], 1)
        return
    parts = rest.split()
    num = 1
    if parts and parts[-1].isdigit():
        num = min(int(parts[-1]), 10)
        parts = parts[:-1]
    tags = [p for p in parts if p.lower() not in _BLOCK_LOWER]
    await _send_lolicon(ctx, tags, num)


@register(keywords=["随机龙"], help="本地龙图随机一张喵", matcher=_exact("随机龙"), role=ROLE_ALL, exact=True)
async def cmd_dragon(ctx):
    await _send_dragon(ctx)


def _nailong_matcher(t):
    return ((t or "").strip() in ("随机奶龙", "来只奶龙", "奶龙", "龙来"))


@register(keywords=["随机奶龙"], help="随机发一张奶龙图喵", matcher=_nailong_matcher, role=ROLE_ALL, exact=True)
async def cmd_nailong(ctx):
    await _send_nailong(ctx)


@register(keywords=["抽猪"], help="随机抽一只猪猪喵", matcher=_exact("抽猪"), role=ROLE_ALL, exact=True)
async def cmd_pighub(ctx):
    await _send_pighub(ctx)
