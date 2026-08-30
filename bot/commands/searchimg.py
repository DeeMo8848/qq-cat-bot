# -*- coding: utf-8 -*-
"""「搜图 / 以图搜图」命令模块：统合多个免费引擎，支持分场景精确搜索。

引擎（参考 astrbot_plugin_search_anime / reverse_searcher 移植，全部免费直连）：
  · trace.moe   → 以图搜番剧（定位番名 + 集数 + 时间点）
  · AnimeTrace  → 以图识动漫角色（角色名 + 作品名）
  · SauceNAO    → 以图搜综合出处（作品/插画来源，HTML 解析，无需 API key）

命令：
  · 搜图    → 三引擎一起搜（番剧 + 角色 + 出处），结果最全但最慢
  · 搜番    → trace.moe 主搜番剧；若无命中再回退 SauceNAO 找出处
  · 搜角色  → 仅 AnimeTrace 识角色
  · 搜出处  → 仅 SauceNAO 找作品/出处

触发时需附带图片、引用一张图片，或图片链接（示例：搜番 https://…）。
「其他功能」子菜单也在本文件注册，列出以上命令与 steamid。
"""

import asyncio
import logging
import os
import uuid

import aiohttp
from bs4 import BeautifulSoup

from config import ROOT
from . import register, ROLE_ALL

_log = logging.getLogger("searchimg")

_TMP_DIR = os.path.join(ROOT, "tmp", "searchimg")
os.makedirs(_TMP_DIR, exist_ok=True)

_TIMEOUT = aiohttp.ClientTimeout(total=25)

_BROWSER_HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
}

_TRACE_MOE = "https://api.trace.moe/search"
_ANIMETRACE = "https://api.animetrace.com/v1/search"
_SAUCENAO = "https://saucenao.com/search.php"

# 「其他功能」子菜单：搜图系列 + steamid（steamid 解析程序不动，仅列条目）
_OTHER_MENU = "\n".join([
    "📦️ 其他功能",
    "· 搜图    → 番剧+角色+出处一起搜喵",
    "· 搜番    → trace.moe 搜番剧（无命中回退出处）",
    "· 搜角色  → AnimeTrace 识动漫角色",
    "· 搜出处  → SauceNAO 找作品出处",
    "· steamid {appid} → 查询 Steam 应用信息",
])


def _d(x):
    """把任意值安全当成 dict 处理，避免 API 返回「非预期类型」时崩溃。"""
    return x if isinstance(x, dict) else {}


def _kw_matcher(kw):
    """精确触发词匹配：仅「搜图」或「搜图 参数」触发，避免子串误触发。"""
    k = kw.strip()
    return lambda t: (t.strip() == k) or t.strip().startswith(k + " ")


# ---------- 图片定位 ----------
def _extract_image_url(ctx):
    """取第一张图片 URL：附件优先，其次参数里的 http 链接，最后回扫消息正文。"""
    m = getattr(ctx, "message", None)
    urls = getattr(m, "image_urls", None) or []
    if urls:
        return urls[0]

    def _scan(text):
        for w in (text or "").split():
            w = w.strip().rstrip("，。,.；;")
            if w.startswith(("http://", "https://")):
                return w
        return None

    hit = _scan(getattr(ctx, "args", ""))
    if not hit:
        hit = _scan(getattr(ctx.message, "content", "")) or _scan(
            getattr(ctx.message, "raw_content", "")
        )
    return hit


async def _fetch_bytes(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=_TIMEOUT, ssl=False, headers=_BROWSER_HDRS) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


# ---------- 引擎 1：trace.moe 搜番 ----------
def _time_convert(t):
    try:
        m, s = divmod(int(t), 60)
    except Exception:
        return "?"
    return f"{int(m)}分{int(s)}秒"


def _sim_percent(v):
    try:
        return f"{float(v) * 100:.1f}%"
    except Exception:
        return "?"


async def _search_trace_moe(img_url):
    """返回 (lines, 精准截图url) 或 None。"""
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.get(
                _TRACE_MOE, params={"url": img_url}, timeout=_TIMEOUT, ssl=False
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        results = _d(data).get("result")
        if not isinstance(results, list) or not results:
            return None
        lines = []
        shot = ""
        for item in results[:3]:
            item = _d(item)
            anilist = _d(item.get("anilist"))
            titles = _d(anilist.get("title"))
            title = (
                titles.get("native")
                or titles.get("chinese")
                or titles.get("romaji")
                or titles.get("english")
                or "未知番剧"
            )
            ep = item.get("episode") or "未知"
            sim = _sim_percent(item.get("similarity"))
            lines.append(
                f"· {title}（第{ep}集）相似度 {sim}  "
                f"时间 {_time_convert(item.get('from', 0))} - "
                f"{_time_convert(item.get('to', 0))}"
            )
            if not shot:
                shot = item.get("image", "")
        return lines, shot
    except Exception as e:
        _log.warning("trace.moe 解析失败: %s", e)
        return None


# ---------- 引擎 2：AnimeTrace 识角色 ----------
async def _search_animetrace(img_url):
    """返回角色行列表 或 None。"""
    try:
        payload = {"url": img_url, "is_multi": 1}
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.post(
                _ANIMETRACE, json=payload, timeout=_TIMEOUT, ssl=False
            ) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        code = _d(data).get("code")
        if code not in (None, 0):  # 成功=0；部分版本可能缺 code 字段
            return None
        items = _d(data).get("data")
        if not isinstance(items, list) or not items:
            return None
        seen = set()
        lines = []
        for it in items:
            for ch in (_d(it).get("character") or []):
                ch = _d(ch)
                work = str(ch.get("work") or "").strip()
                name = str(ch.get("character") or "").strip()
                key = (work, name)
                if not (work or name) or key in seen:
                    continue
                seen.add(key)
                lines.append(f"· {name}（出自《{work}》）")
                if len(lines) >= 3:
                    return lines
        return lines or None
    except Exception as e:
        _log.warning("AnimeTrace 解析失败: %s", e)
        return None


# ---------- 引擎 3：SauceNAO 找出处 ----------
def _parse_saucenao_html(html_text):
    """解析 SauceNAO 网页结果，返回 [(标题, 相似度, [链接])]，无结果返回 None。"""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception:
        return None
    items = []
    for res in soup.find_all("div", class_="result"):
        if res.get("id") == "result-hidden-notification":
            continue
        sim_elem = res.find("div", class_="resultsimilarityinfo")
        if not sim_elem:
            continue
        sim = sim_elem.get_text(strip=True)
        title_elem = res.find("div", class_="resulttitle")
        title = title_elem.get_text(strip=True) if title_elem else "未知作品"
        links = []
        for col in res.find_all("div", class_="resultcontentcolumn"):
            for a in col.find_all("a"):
                href = (a.get("href") or "").strip()
                if href.startswith("http") and "saucenao.com/info.php" not in href:
                    links.append(href)
        items.append((title, sim, links))
    return items or None


async def _search_saucenao(img_url):
    """返回出处行列表 或 None。"""
    try:
        form = aiohttp.FormData()
        form.add_field("url", img_url)
        form.add_field("db", "999")
        form.add_field("numres", "3")
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.post(
                _SAUCENAO, data=form, timeout=_TIMEOUT, ssl=False
            ) as r:
                if r.status != 200:
                    return None
                text = await r.text()
        parsed = _parse_saucenao_html(text)
        if not parsed:
            return None
        lines = []
        for title, sim, links in parsed[:3]:
            line = f"· {title}  相似度 {sim}"
            if links:
                line += f"  → {links[0]}"
            lines.append(line)
        return lines
    except Exception as e:
        _log.warning("SauceNAO 解析失败: %s", e)
        return None


# ---------- 结果汇总与发送 ----------
async def _send_report(ctx, head, sections):
    """sections: [(节标题, 结果)]，结果可为行列表或 (lines, shot) 元组。"""
    lines = [f"🔍 {head}结果："]
    shot = ""
    any_hit = False
    for label, res in sections:
        lines.append(f"\n【{label}】")
        if res is None:
            lines.append("· 未识别到")
            continue
        if isinstance(res, tuple):  # trace.moe → (lines, shot)
            lines.extend(res[0])
            if not shot:
                shot = res[1]
        else:
            lines.extend(res)
        any_hit = True
    text = "\n".join(lines)

    shot_bytes = await _fetch_bytes(shot) if shot else None
    if shot_bytes:
        path = os.path.join(_TMP_DIR, uuid.uuid4().hex + ".jpg")
        try:
            with open(path, "wb") as f:
                f.write(shot_bytes)
            await ctx.sender.send_image_with_text(ctx.message, text, path, reply=False)
            return
        except Exception:
            pass
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
    await ctx.reply_text(text)


async def _require_image(ctx):
    img = _extract_image_url(ctx)
    if not img:
        await ctx.reply(
            "请附带图片、引用一张图片或给图片链接喵（如：搜图 https://…）"
        )
        return None
    await ctx.reply_text("🔍 在帮主人翻图鉴喵，稍等片刻~")
    return img


# ---------- 命令注册 ----------
@register(keywords=["搜图"], help="以图搜番剧/角色/出处一起搜喵", matcher=_kw_matcher("搜图"), role=ROLE_ALL)
async def cmd_searchimg(ctx):
    img = await _require_image(ctx)
    if not img:
        return
    t_res, a_res, s_res = await asyncio.gather(
        _search_trace_moe(img), _search_animetrace(img), _search_saucenao(img)
    )
    await _send_report(
        ctx,
        "搜图",
        [("📺 以图搜番", t_res), ("🎭 角色识别", a_res), ("🎨 出处搜索", s_res)],
    )


@register(keywords=["搜番"], help="trace.moe搜番剧（无命中回退出处）喵", matcher=_kw_matcher("搜番"), role=ROLE_ALL)
async def cmd_search_anime(ctx):
    img = await _require_image(ctx)
    if not img:
        return
    t_res = await _search_trace_moe(img)
    if t_res is None:
        s_res = await _search_saucenao(img)
        await _send_report(ctx, "搜番", [("📺 以图搜番", t_res), ("🎨 回退出处", s_res)])
        return
    await _send_report(ctx, "搜番", [("📺 以图搜番", t_res)])


@register(keywords=["搜角色"], help="AnimeTrace识动漫角色喵", matcher=_kw_matcher("搜角色"), role=ROLE_ALL)
async def cmd_search_char(ctx):
    img = await _require_image(ctx)
    if not img:
        return
    a_res = await _search_animetrace(img)
    await _send_report(ctx, "搜角色", [("🎭 角色识别", a_res)])


@register(keywords=["搜出处"], help="SauceNAO找作品出处喵", matcher=_kw_matcher("搜出处"), role=ROLE_ALL)
async def cmd_search_source(ctx):
    img = await _require_image(ctx)
    if not img:
        return
    s_res = await _search_saucenao(img)
    await _send_report(ctx, "搜出处", [("🎨 出处搜索", s_res)])


@register(keywords=["其他功能"], help="其他功能清单喵", matcher=_kw_matcher("其他功能"), role=ROLE_ALL)
async def cmd_other_menu(ctx):
    await ctx.reply(_OTHER_MENU)