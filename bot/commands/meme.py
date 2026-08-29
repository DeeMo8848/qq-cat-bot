# -*- coding: utf-8 -*-
"""镜像 meme 生成的全局开关逻辑，避免 worker 被踩进 meme 未启用时还生成。

本文件是「meme」功能的实现：
    · meme列表          -> 渲染所有关键词为图片
    · meme搜索 (名称)    -> 返回关键词/图片数/文本数/可选值
    · meme更新          -> 尝试更新本地 meme 并重建关键词数据
    · 直接发关键词       -> 制作对应表情包并发送
"""

import os
import re
import time
import json
import random
import hashlib

from . import register, ROLE_ALL
from bot.core import tools, members
from bot.meme.meme_data import KW, META
from config import ROOT, PYTHON

PY_EXE = PYTHON
_PROJ_ROOT = ROOT
WORKER = os.path.join(_PROJ_ROOT, "bot", "meme", "meme_worker.py")
RENDER = os.path.join(_PROJ_ROOT, "bot", "meme", "render.py")
REBUILD = os.path.join(_PROJ_ROOT, "bot", "meme", "rebuild_data.py")
_TMP_ROOT = os.path.join(_PROJ_ROOT, "tmp", "meme")
# meme列表缓存：模板没变化时复用已生成的图片，避免每次重新渲染
_CACHE_DIR = os.path.join(_PROJ_ROOT, "cache")
_CACHE_FP = os.path.join(_CACHE_DIR, "meme_list.json")
# 列表渲染格式版本：换渲染器（官方风格）后改这里，让旧缓存作废、重新渲染
_LIST_FORMAT = "official_render"

TUTORIAL = (
    "🐱 表情包(meme)使用教程\n"
    "· meme列表          → 列出所有可用表情包关键词喵\n"
    "· meme搜索 (名称)    → 查看某个表情包的信息喵\n"
    "· 随机meme          → 随机出一个表情包喵\n"
    "· meme更新          → 联网更新并整理表情包喵\n"
    "· meme刷新          → 仅重新整理本地表情包喵\n"
    "· 直接发关键词       → 制作对应表情包喵\n"
    "示例：5000兆 / 滚@某人 / 夏日琉璃子 @某人 文本"
)

_CMD = re.compile(r"^meme\s*", re.I)
_RANDOM_CMD = re.compile(r"随机\s*meme\s*", re.I)
_HTTP_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
# QQ 图床链接通常没有图片扩展名，按域名/特征判断是否像图片
_IMG_LIKE = re.compile(
    r"(?:\.(?:png|jpe?g|gif|webp|bmp)(?:[?#]|$)|qpic\.cn|gchatpic|term=)", re.I
)


def _find_image_url(text: str):
    """从一段文本里找出第一个像图片的 URL；找不到返回 None。"""
    for u in _HTTP_URL.findall(text or ""):
        u = re.sub(r"[)\]}><]+$", "", u)
        if _IMG_LIKE.search(u):
            return u
    return None


def _fresh_tmp():
    d = os.path.join(_TMP_ROOT, str(int(time.time() * 1000)))
    os.makedirs(d, exist_ok=True)
    return d


def _match_meme(text: str):
    """返回 (关键词, key) 或 None。支持「关键词」或「关键词 + 参数(@/文本)」。"""
    t = (text or "").strip()
    for kw in sorted(KW, key=len, reverse=True):
        if not t.startswith(kw):
            continue
        if len(t) == len(kw) or t[len(kw)] in " <@":
            return kw, KW[kw]
    return None


def is_meme(text: str) -> bool:
    """消息文本是否为可触发的 meme 关键词（含后接参数）。"""
    return _match_meme(text) is not None


def _matcher(text):
    t = (text or "").strip()
    if _RANDOM_CMD.match(t):
        return True
    if _CMD.match(t):
        return True
    return _match_meme(t) is not None


@register(keywords=["meme"], help="制作各种表情包呢喵", matcher=_matcher, role=ROLE_ALL, exact=True)
async def cmd_meme(ctx):
    text = (ctx.args or "").strip()
    if _RANDOM_CMD.match(text):
        await _random(ctx)
        return
    m = _CMD.match(text)
    if m:
        rest = text[m.end():].strip()
        if rest.startswith("list") or rest.startswith("列表") or (rest and ("列表" in rest or "list" in rest.lower())):
            await _list(ctx)
            return
        if "更新" in rest or rest.lower().startswith("update"):
            await _update(ctx)
            return
        if "刷新" in rest or rest.lower().startswith("refresh"):
            await _refresh(ctx)
            return
        if "搜索" in rest:
            name = rest.split("搜索", 1)[1].strip() or rest.replace("搜索", "").strip()
            await _search(ctx, name)
            return
        # meme 后无有效子命令
        key = None
        if rest:
            _mm = _match_meme(rest)
            if _mm:
                key = _mm[1]
        if key:
            await _make(ctx, key, rest)
        else:
            await ctx.reply(TUTORIAL)
        return

    mm = _match_meme(text)
    if mm:
        await _make(ctx, mm[1], text)
    else:
        await ctx.reply(TUTORIAL)


# ---------- 列表 ----------
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _template_rows():
    """每个模板一行（以其主要关键词表示，避免别名膨胀成几百行）。"""
    rows = set()
    for key in META:
        kws = [k for k, v in KW.items() if v == key]
        main = next((k for k in kws if _CJK.search(k)), (kws[0] if kws else key))
        rows.add(main)
    return sorted(rows)


async def _load_cache():
    """读取列表缓存；文件损坏返回 {}。"""
    if not os.path.exists(_CACHE_FP):
        return {}
    try:
        with open(_CACHE_FP, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FP, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _list(ctx):
    rows = _template_rows()
    if not rows:
        await ctx.reply("当前没有可用的表情包喵")
        return
    # 以「模板内容 + 渲染格式」哈希判断是否需要重绘：换格式/增删 meme 都会重新生成
    template_hash = hashlib.md5(
        (_LIST_FORMAT + "\n" + "\n".join(rows)).encode("utf-8")
    ).hexdigest()
    cache = await _load_cache()
    if cache.get("hash") == template_hash and cache.get("files"):
        files = [p for p in cache["files"] if os.path.exists(p)]
        if len(files) == len(cache["files"]):
            for p in files:
                result = await ctx.sender.send_local_file(ctx.message, 1, p)
                if isinstance(result, str):
                    await ctx.reply_text(result)
            return
    # 未命中或文件失效：重新渲染；产物存入持久 cache/（跨重启保留，仅模板变化时重绘）
    os.makedirs(_CACHE_DIR, exist_ok=True)
    txt = os.path.join(_CACHE_DIR, "meme_list_kw.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))
    outbase = os.path.join(_CACHE_DIR, "meme_list")
    out, err, code = await tools.run_script(
        f'"{PY_EXE}" "{RENDER}" --in "{txt}" --out "{outbase}"', timeout=120
    )
    try:
        files = json.loads(out.strip().splitlines()[-1])["files"]
    except Exception:
        await ctx.reply_text("呜，列表生成失败喵")
        return
    _save_cache({"hash": template_hash, "files": files, "time": time.time()})
    for p in files:
        result = await ctx.sender.send_local_file(ctx.message, 1, p)
        if isinstance(result, str):
            await ctx.reply_text(result)


# ---------- 搜索信息 ----------
def _info_text(key: str) -> str:
    meta = META.get(key)
    if not meta:
        return "喵，没有这个表情喵"
    kws = [k for k, v in KW.items() if v == key]
    def rng(a, b):
        return str(a) if a == b else f"{a} ~ {b}"
    lines = ["关键词：" + "、".join(kws) if kws else "关键词：(无)"]
    lines.append(f"需要图片数目：{rng(meta['min_images'], meta['max_images'])}")
    lines.append(f"需要文字数目：{rng(meta['min_texts'], meta['max_texts'])}")
    opts = meta.get("args_options") or []
    if opts:
        lines.append("可选值：" + " / ".join(opts))
    return "\n".join(lines)


async def _search(ctx, name):
    name = (name or "").strip()
    if not name:
        await ctx.reply(TUTORIAL)
        return
    keys = set()
    if name in KW:
        keys.add(KW[name])
    if name in META:
        keys.add(name)
    for k, v in KW.items():
        if name in k:
            keys.add(v)
    keys = sorted(keys)
    if not keys:
        await ctx.reply("猫猫掘地三尺没有找到该meme喵")
        return
    if len(keys) == 1:
        await ctx.reply(_info_text(keys[0]))
        return
    # 多个匹配：合并成一条紧凑列表，避免刷屏（超 30 条收敛提示）
    lines = [f"找到 {len(keys)} 个含「{name}」的表情喵："]
    shown = 0
    for key in keys:
        if shown >= 30:
            lines.append(f"…等共 {len(keys)} 个，输入精确关键词可看详情喵")
            break
        kws = [k for k, v in KW.items() if v == key]
        main = next((k for k in kws if _CJK.search(k)), (kws[0] if kws else key))
        meta = META.get(key, {})
        mi, ma = meta.get("min_images", 0), meta.get("max_images", 0)
        ti, ta = meta.get("min_texts", 0), meta.get("max_texts", 0)
        lines.append(f"· {main}（图{mi}~{ma} 文{ti}~{ta}）")
        shown += 1
    await ctx.reply("\n".join(lines))


# ---------- 图片解析 ----------
async def _download_image(url: str, save_path: str):
    if not url:
        return None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                if r.status != 200:
                    return None
                data = await r.read()
        if not data or len(data) < 20:
            return None
        ext = ".png"
        if data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif data[:4] == b"GIF8":
            ext = ".gif"
        p = save_path + ext
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:
        return None


async def _resolve_images(ctx, workdir: str, need: int):
    """按用户规则组装 meme 的图片参数，返回图片路径列表（不足时数量 < need）。"""
    if need <= 0:
        return []
    group = getattr(ctx, "target", None)
    api = ctx.sender.api

    # 被@的"别人"（排除机器人自己）。注意 QQ mention 里的 openid 字段是 member_openid，
    # 且 mentions 数组顺序可能与用户输入时的视觉顺序不一致，故按内容里 <@openid> 标签顺序恢复。
    mention = {}
    for m in getattr(ctx.message, "mentions", None) or []:
        if isinstance(m, dict):
            oid = m.get("member_openid") or m.get("openid") or m.get("id") or ""
            is_you = m.get("is_you")
        else:
            oid = (
                getattr(m, "member_openid", None)
                or getattr(m, "openid", None)
                or ""
            )
            is_you = getattr(m, "is_you", None)
        if oid:
            mention[oid] = is_you

    raw = getattr(ctx.message, "raw_content", None) if getattr(ctx.message, "raw_content", None) is not None else ""
    if not raw:
        raw = getattr(ctx.message, "content", "") or ""
    ats = [oid for oid in re.findall(r"<@([0-9A-Fa-f]+)>", raw) if mention.get(oid) is False]
    if not ats:  # 内容里没有 @ 标签时，回退按 mentions 数组顺序
        ats = [oid for oid, is_you in mention.items() if not is_you]

    ref_url = ""
    # 优先使用消息里收集到的图片（顶层附件 + msg_elements 内嵌套附件的 URL）
    pending = getattr(ctx.message, "image_urls", None)
    if pending is None:  # 非 webhook 消息没有 image_urls，退回读 attachments
        pending = []
        for att in getattr(ctx.message, "attachments", None) or []:
            u = att.get("url") if isinstance(att, dict) else getattr(att, "url", None)
            if u:
                pending.append(u)
    for u in pending:
        if u:
            ref_url = u
            break
    if not ref_url:
        ref_url = _find_image_url(getattr(ctx.message, "content", "") or "") or ""

    _av = {"n": 0}

    async def av(openid):
        _av["n"] += 1
        url = await members.get_member_avatar(api, group, openid)
        return await _download_image(url, os.path.join(workdir, f"av{_av['n']}"))

    if need == 1:
        if ref_url:
            return [await _download_image(ref_url, os.path.join(workdir, "ref"))]
        if ats:
            return [await av(ats[0])]
        return [await av(getattr(ctx, "openid", ""))]
    if need == 2:
        ref = await _download_image(ref_url, os.path.join(workdir, "ref")) if ref_url else None
        if ref:
            if ats:
                return [ref, await av(ats[0])]
            # 平台在「@机器人」事件里会丢掉 @ 对象信息（content 无 <@>、mentions 为空），
            # 这时无法取到被@者头像，退回用发送者自己的头像补足第二张。
            return [ref, await av(getattr(ctx, "openid", ""))]
        if not ats:
            return []
        if len(ats) == 1:
            return [await av(getattr(ctx, "openid", "")), await av(ats[0])]
        return [await av(ats[0]), await av(ats[1])]
    # need >= 3：通用填充
    seq = []
    if ref_url:
        seq.append(await _download_image(ref_url, os.path.join(workdir, "ref")))
    for o in ats:
        seq.append(await av(o))
    seq.append(await av(getattr(ctx, "openid", "")))
    return seq[:need]


# ---------- 文本解析 ----------
def _resolve_texts(key: str, raw_text: str, kw: str):
    meta = META[key]
    min_t, max_t = meta["min_texts"], meta["max_texts"]
    rest = raw_text[len(kw):].strip()
    rest = re.sub(r"<@[^>]+>", " ", rest)
    rest = re.sub(r"https?://\S+", " ", rest)
    words = [w for w in rest.split() if w]
    if min_t <= len(words) <= max_t:
        return words, None
    default_ts = meta.get("default_texts") or []
    if not words and default_ts and min_t <= len(default_ts) <= max_t:
        return list(default_ts), None
    if max_t == 0:
        return [], "制作失败，该meme不允许添加文本喵"
    num = f"{min_t} ~ {max_t} 条" if min_t != max_t else f"{min_t} 条"
    ex = f"{kw} " + " ".join(f"文本{j + 1}" for j in range(min_t))
    return [], f"制作失败，该meme需要{num}文本喵，例如：{ex}"


# ---------- 随机 ----------
def _random_candidates():
    """可安全随机的模板：图片≤1张（可用触发者头像补足），文字要么 0 条要么有默认文本。"""
    cands = []
    for key, meta in META.items():
        if key not in KW.values():
            continue  # 该模板没有可触发的关键词，无法走正常生成流程
        if meta.get("min_images", 0) > 1:
            continue
        mi, ma = meta.get("min_texts", 0), meta.get("max_texts", 0)
        if mi == 0:
            cands.append(key)
        elif meta.get("default_texts") and mi <= len(meta["default_texts"]) <= ma:
            cands.append(key)
    return cands


async def _random(ctx):
    cands = _random_candidates()
    if not cands:
        await ctx.reply_text("呜，没有能随机出的表情包喵")
        return
    key = random.choice(cands)
    # 取该模板的一个中文关键词作为触发词，让默认文本 / 触发者头像自动填充
    kw = next((k for k, v in KW.items() if v == key and _CJK.search(k)), None)
    if not kw:
        kw = next((k for k, v in KW.items() if v == key), "")
    await _make(ctx, key, kw)


# ---------- 制作 ----------
async def _make(ctx, key, raw_text):
    # 取「能匹配到的最长关键词」，避免把长词截成短词+残余文本（如 亲亲 别被切成 亲+亲）
    kw = max(
        (k for k, v in KW.items() if v == key and raw_text.startswith(k)),
        key=len,
        default=raw_text,
    )
    meta = META[key]
    min_i = meta["min_images"]

    workdir = _fresh_tmp()
    try:
        images = await _resolve_images(ctx, workdir, min_i)
        images = [p for p in images if p]  # 过滤拿不到头像/图片产生的 None
        if len(images) < min_i:
            await ctx.reply_text(f"制作失败，该meme需要{min_i}张图片喵")
            return

        texts, err = _resolve_texts(key, raw_text, kw)
        if err:
            await ctx.reply_text(err)
            return

        outbase = os.path.join(workdir, "out")
        cmd = f'"{PY_EXE}" "{WORKER}" --key "{key}" --out "{outbase}"'
        for p in images:
            cmd += f' --image "{p}"'
        for t in texts:
            cmd += f' --text "{t}"'
        out, err_out, code = await tools.run_script(cmd, timeout=120)

        res = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    res = json.loads(line)
                    break
                except Exception:
                    continue
        if res and res.get("ok"):
            path = res["file"]
            result = await ctx.sender.send_local_file(ctx.message, 1, path)
            if isinstance(result, str):
                await ctx.reply_text(result)
        else:
            msg = (res or {}).get("error") or "未知错误"
            await ctx.reply_text(f"呜，制作卡崩了喵：{msg}")
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)


# ---------- 更新 / 刷新 ----------
def _reload_data():
    """重新读取 meme_data.py，让新模板/关键词立即生效（无需重启 bot）。"""
    global KW, META
    import importlib
    import bot.meme.meme_data as md
    try:
        importlib.reload(md)
        KW = md.KW
        META = md.META
        return True
    except Exception:
        return False


async def _refresh(ctx):
    """仅整理本地 meme 内容（列表/字典/清单），不联网。"""
    rebuild = await tools.run_script(f'"{PY_EXE}" "{REBUILD}"', timeout=120)
    ok = rebuild[2] == 0 and os.path.exists(
        os.path.join(_PROJ_ROOT, "bot", "meme", "meme_data.py")
    )
    if ok:
        _reload_data()  # 让运行中的 bot 直接使用新数据；模板若增删会自动重绘列表图
        await ctx.reply_text("meme整理好了喵")
    else:
        await ctx.reply_text("呜，meme整理失败喵")


async def _update(ctx):
    await ctx.reply_text("🐱 正在整理 meme 喵，请稍候…")
    # 步骤A：联网确保 meme-generator 为受控版本 0.1.14（不升 rs 版）。
    #   pip 返回码 0 = 已是最新 / 成功装好；非 0 = 网络等原因未能拿到。
    a_ok = (await tools.run_script(
        f'"{PY_EXE}" -m pip install "meme-generator==0.1.14"', timeout=240
    ))[2] == 0
    # 步骤B：整理本地数据（列表/字典/清单）。
    rebuild = await tools.run_script(f'"{PY_EXE}" "{REBUILD}"', timeout=120)
    b_ok = rebuild[2] == 0 and os.path.exists(
        os.path.join(_PROJ_ROOT, "bot", "meme", "meme_data.py")
    )
    if b_ok:
        _reload_data()  # 整理成功后热重载，模板增删时会自动重绘列表图
    if a_ok and b_ok:
        await ctx.reply_text("猫猫把meme更新好了喵")
    elif a_ok and not b_ok:
        await ctx.reply_text("猫猫找到了新meme，但没能整理好喵")
    elif not a_ok and b_ok:
        await ctx.reply_text("魔法把猫猫拒之门外了喵，但本喵把meme整理好了喵")
    else:
        await ctx.reply_text("魔法把猫猫拒之门外了喵，猫猫搞砸了喵")