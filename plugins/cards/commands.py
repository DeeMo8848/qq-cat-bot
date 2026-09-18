# -*- coding: utf-8 -*-
"""🃏 卡牌制作命令：制作卡牌 / 卡牌制作 / 收集册 / 我的卡牌 / 销毁卡牌 /
卡牌市场 / 上架卡牌 / 下架卡牌 / 购买卡牌 / 素材包 / 购买素材。

流程：引用图片 + 「卡牌制作 名称 [参数]」→ 扣制作费 → 进串行队列
→ worker：下载图 → AI 判品级（视觉模型）→ cardforge 出卡 → 资产入库 → 回链接。
"""

import json
import logging
import os
import re
import time

from config import ROOT, STATIC_PUBLIC_URL
from bot.commands import register, ROLE_ALL
from bot.core import wallet

from . import carddata as cd
from . import forge

_log = logging.getLogger("cards")

DEFAULT_MAKE_COST = 200      # 制作费（settings.json CARD_MAKE_COST 可改）
DEFAULT_USE_FEE = 100        # 每个素材使用费（settings.json CARD_MATERIAL_USE_FEE 可改）
DEFAULT_PRICES = {           # 素材包定价（settings.json CARD_MATERIAL_PRICES 可改）
    "background": 500, "frame": 800, "seal": 1000, "back": 600, "glow": 800,
}
TAX = 0.1                    # 市场交易税（settings.json CARD_MARKET_TAX 可改）

_TMP_DIR = os.path.join(ROOT, "tmp", "cards")
os.makedirs(_TMP_DIR, exist_ok=True)

# 素材类型：显示名 -> (类型key, 中文名)
MATERIAL_CATS = {
    "背景": ("background", "背景"),
    "边框": ("frame", "边框"),
    "卡封": ("seal", "卡封"),
    "牌背": ("back", "牌背"),
    "边框特效": ("glow", "边框特效"),
}
_CAT_CN = {v[0]: k for k, v in MATERIAL_CATS.items()}

# 快捷购买命令词 → 素材类型（「购买辉光」→ glow，辉光是边框特效的别名）
_BUY_KW_CAT = {
    "购买背景": "background", "购买边框": "frame",
    "购买牌背": "back", "购买卡封": "seal", "购买辉光": "glow",
}

# 卡牌制作参数关键词（按出现位置解析；「扣图/不扣图」为「抠图/不抠图」的同音容错写法）
_PARAM_KEYWORDS = ["卡牌特效", "边框特效", "卡牌类型", "背景", "边框", "牌背",
                   "卡封", "缩放", "整幅", "透明", "抠图", "不抠图", "扣图", "不扣图",
                   "裁剪", "不裁剪", "自适应", "辉光强度", "主体悬浮", "描边",
                   "文本型", "特殊文本型", "无文本型", "标题", "描述", "跳过评估"]

# 基础素材：默认所有人可用、无需购买、不标价（按文件名 stem 匹配，如 sample-1.png → sample-1）
FREE_MATERIALS = {"sample-1"}


def _is_free(cat_key: str, canon: str) -> bool:
    """基础素材判定。"""
    return cat_key != "glow" and os.path.splitext(canon)[0] in FREE_MATERIALS


def _settings() -> dict:
    try:
        with open(os.path.join(ROOT, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _make_cost() -> int:
    try:
        return int(_settings().get("CARD_MAKE_COST", DEFAULT_MAKE_COST))
    except (TypeError, ValueError):
        return DEFAULT_MAKE_COST


def _use_fee() -> int:
    """每个素材的使用费（settings CARD_MATERIAL_USE_FEE，默认 100，0=不加收）。"""
    try:
        return max(0, int(_settings().get("CARD_MATERIAL_USE_FEE", DEFAULT_USE_FEE)))
    except (TypeError, ValueError):
        return DEFAULT_USE_FEE


def _cooldown_min() -> int:
    """制作冷却分钟数（settings CARD_MAKE_COOLDOWN，0=关闭）。"""
    try:
        return max(0, int(_settings().get("CARD_MAKE_COOLDOWN", 0)))
    except (TypeError, ValueError):
        return 0


def _prices() -> dict:
    p = dict(DEFAULT_PRICES)
    try:
        p.update({k: int(v) for k, v in (_settings().get("CARD_MATERIAL_PRICES") or {}).items()})
    except (TypeError, ValueError):
        pass
    return p


def _tax() -> float:
    """市场交易税（settings.json CARD_MARKET_TAX 可改，代码默认 0.1）。"""
    try:
        return float(_settings().get("CARD_MARKET_TAX", TAX))
    except (TypeError, ValueError):
        return TAX


# ---------- 图片提取（复用下载图片插件的深度提取） ----------
try:
    from plugins.downloadimg import downloadimg as _dl
    _deep_image_urls = _dl._deep_image_urls
    _fetch_image = _dl._fetch_bytes
except Exception:
    def _deep_image_urls(message):
        return list(getattr(message, "image_urls", None) or [])

    async def _fetch_image(url):
        import aiohttp
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=40), ssl=False) as r:
                    return await r.read() if r.status == 200 else None
        except Exception:
            return None


def _nick_of(message) -> str:
    author = getattr(message, "author", None)
    if isinstance(author, dict):
        return author.get("member_nick") or author.get("nick") or author.get("username") or ""
    return (getattr(author, "member_nick", None) or getattr(author, "nick", None)
            or getattr(author, "username", None) or "")


# ---------- 参数解析 ----------

def _split_name_params(rest: str):
    """把「名称 + 参数」分开：名称 = 第一个参数关键词之前的部分。"""
    pos = len(rest)
    for kw in _PARAM_KEYWORDS:
        i = rest.find(kw)
        if 0 <= i < pos:
            pos = i
    return rest[:pos].strip(), rest[pos:].strip()


def _parse_params(s: str) -> dict:
    """顺序提取参数关键词及其值（'kw值' / 'kw 值' / 'kw'=无值置 None）。"""
    out = {}
    s = (s or "").strip()
    while s:
        best_kw, best_i = None, len(s) + 1
        for kw in _PARAM_KEYWORDS:
            i = s.find(kw)
            if 0 <= i < best_i:
                best_kw, best_i = kw, i
        if best_kw is None:
            break
        tail = s[best_i + len(best_kw):].strip()
        value = None
        nxt = tail.split(None, 1)
        if nxt and not any(nxt[0].startswith(k) for k in _PARAM_KEYWORDS):
            value = nxt[0]
            tail = nxt[1] if len(nxt) > 1 else ""
        out[best_kw] = value
        s = tail
    return out


_TEXT_TYPE_MAP = {"文本": "boxed", "特殊文本": "transparent", "无文本": "none"}
# 直接关键词写法（「文本型」等），与「卡牌类型xx」等价
_TEXT_KW = {"文本型": "boxed", "特殊文本型": "transparent", "无文本型": "none"}


def _resolve_material(cat_key: str, name: str, openid):
    """校验素材存在 + 已解锁。返回 (canonical_name, 错误信息)。"""
    if not name:
        return None, ""
    if cat_key == "glow":
        if name in ("none", "无", "无特效"):
            return None, ""  # 关闭辉光，无需解锁
        if name not in forge.GLOW_BUILTINS:
            return None, f"边框特效「{name}」不存在（可选：{'/'.join(forge.GLOW_BUILTINS)}）"
        if not cd.owns_material(openid, "glow", name):
            return None, f"边框特效「{name}」未解锁，先发「购买素材 边框特效 {name}」"
        return name, ""
    p = forge.material_path(cat_key, name)
    if not p:
        return None, f"{_CAT_CN.get(cat_key, cat_key)}素材「{name}」不存在，发「素材包」查看可买素材"
    canon = os.path.basename(p)
    if _is_free(cat_key, canon):
        return canon, ""  # 基础素材默认可用
    if not cd.owns_material(openid, cat_key, canon):
        return None, f"{_CAT_CN.get(cat_key, cat_key)}「{canon}」未解锁，先发「购买素材 {_CAT_CN.get(cat_key, cat_key)} {canon}」"
    return canon, ""


# ---------- 制作任务（串行队列 worker 核心） ----------

async def notify(sender, message, text):
    try:
        await sender.send_text(message, text, reply=True)
    except Exception:
        _log.exception("卡牌通知发送失败")


async def process_make_task(task: dict):
    from . import ai_judge

    sender = task["sender"]
    message = task["message"]
    openid = task["openid"]
    name = task["name"]
    card_key = task["card_key"]

    async def fail(reason):
        wallet.add(openid, task["cost"])
        await notify(sender, message, f"卡牌制作失败喵，制作费已退回：{reason}")

    try:
        await notify(sender, message, "开始制作喵，正在取图…")
        img_bytes = await _fetch_image(task["image_url"])
        if not img_bytes:
            return await fail("图片下载失败")
        ext = "png"
        m = re.search(r"\.([A-Za-z0-9]{2,5})$", (task["image_url"] or "").split("?")[0] or "")
        if m and m.group(1).lower() in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "gif" if m.group(1).lower() == "gif" else m.group(1).lower()
        img_path = os.path.join(_TMP_DIR, f"{card_key}.{ext}")
        with open(img_path, "wb") as f:
            f.write(img_bytes)

        if task["skip_judge"]:
            judge = ai_judge.random_fallback()
            rarity = judge["rarity"]
            title = task["title"] or ""
            desc = task["desc"] or ""
            await notify(sender, message, f"已跳过 AI 评估，随机品级：{rarity}，正在调用 cardforge 制作喵…")
        else:
            await notify(sender, message, "AI 正在判定卡牌品级喵…")
            judge = await ai_judge.judge_card(img_bytes, task["need_text"])
            rarity = judge["rarity"]
            title = task["title"] or judge["title"]
            desc = task["desc"] or judge["desc"]
            await notify(sender, message, f"品级判定完成：{rarity}，正在调用 cardforge 制作喵…")
        args = [img_path, "--name", name, "--slug", card_key, "--json"]
        if task["style"] == "full-bleed":
            args += ["--style", "full-bleed"]
        if task["adaptive"] is not None:
            args += ["--adaptive", str(task["adaptive"])]
        elif task["style"] == "transparent":
            args += ["--adaptive", "2"]  # 透明主体判定：不足留白
        if task["no_matting"]:
            args += ["--model", "none"]
        if task["no_mask"]:
            args += ["--no-mask"]
        if task["float_fg"]:
            args += ["--float-fg"]
        if task["outline"]:
            args += ["--outline"]
        if task["scale"]:
            args += ["--scale", str(task["scale"])]
        if task["glow_strength"] is not None:
            args += ["--glow-strength", str(task["glow_strength"])]
        for kw, val in (("background", task["background"]), ("frame", task["frame"]),
                        ("seal", task["seal"]), ("back", task["back"])):
            if val:
                args += [f"--{kw}", val]
        if task["glow"]:
            args += ["--glow", task["glow"]]
        if task["text_type"]:
            args += ["--text-type", task["text_type"]]
        if title:
            args += ["--title", title]
        if desc:
            args += ["--desc", desc]

        rc, out = await forge._run_forge(args)
        if rc != 0:
            tail = (out or "").strip().splitlines()
            return await fail((tail[-1] if tail else "cardforge 执行失败")[:120])

        result = _parse_forge_json(out)
        if not result or not result.get("dir"):
            return await fail("cardforge 输出异常，请稍后重试")

        forge.copy_output(card_key, result["dir"])
        price = cd.rand_price(rarity)
        cd.add_card({
            "cardKey": card_key,
            "slug": card_key,
            "name": name,
            "rarity": rarity,
            "price": price,
            "owner": openid,
            "owner_nick": task["nick"],
            "created": int(time.time()),
            "style": task["style"],
            "text_type": task["text_type"] or "none",
            "title": title or "",
            "desc": desc or "",
            "materials": {"background": task["background"], "frame": task["frame"],
                          "seal": task["seal"], "back": task["back"], "glow": task["glow"]},
            "status": "owned",
        })
        await notify(sender, message, (
            f"✨ 卡牌制作完成！\n【{name}】{cd.RARITY_EMOJI.get(rarity, '')}{rarity}　价值 {price} 喵币\n"
            f"3D 预览：{STATIC_PUBLIC_URL}/card/{card_key}"))
    except Exception as e:
        _log.exception("制作任务异常")
        await fail(str(e)[:120])


def _parse_forge_json(text: str):
    t = (text or "").strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _resolve_own_card(openid, text):
    """解析用户的卡：唯一命中返回 (card, None)；重名/未找到返回 (None, 提示)。"""
    hits = cd.find_cards(openid, text)
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        lines = [f"你有 {len(hits)} 张同名卡「{hits[0]['name']}」，用下面的卡牌 ID 指定："]
        for c in hits:
            suffix = (f"　【市场 {cd.market_price_of(c['cardKey'])} 喵币】"
                      if c.get("status") == "market" else "")
            lines.append(f"· {c['cardKey']}｜{cd.RARITY_EMOJI.get(c['rarity'], '')}{c['rarity']}｜{c['price']} 喵币{suffix}")
        return None, "\n".join(lines)
    return None, "没找到这张卡喵，发「我的卡牌」看看有哪些"


def _resolve_market_card(text):
    """解析市场里的卡：唯一命中返回 (card, None)；重名/未找到返回 (None, 提示)。"""
    hits = cd.find_market_cards(text)
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        lines = [f"市场里有 {len(hits)} 张同名卡「{hits[0]['name']}」，用下面的卡牌 ID 指定："]
        for c in hits:
            lines.append(f"· {c['cardKey']}｜{cd.RARITY_EMOJI.get(c['rarity'], '')}{c['rarity']}｜{cd.market_price_of(c['cardKey'])} 喵币")
        return None, "\n".join(lines)
    return None, "市场里没找到这张卡喵，发「卡牌市场」看看"


# ---------- 命令 ----------

@register(keywords=["制作卡牌", "卡牌帮助"], help="🃏 卡牌 DIY：查看制作教程喵", role=ROLE_ALL)
async def cmd_card_help(ctx):
    cost = _make_cost()
    await ctx.reply(
        "🃏 卡牌 DIY 教程\n"
        f"引用一张图，发「卡牌制作 卡名 [参数]」制作卡牌（基础费 {cost} 喵币/次 + 每个用到的素材另收使用费，AI 判定品级）\n"
        "可选参数：边框xx 背景xx 牌背xx 卡封xx 边框特效xx 辉光强度0~2 缩放0.8 整幅 透明 抠图 不抠图 "
        "裁剪 不裁剪 自适应0/1/2 主体悬浮 描边 文本型/特殊文本型/无文本型 标题xx 描述xx 跳过评估\n"
        "（默认整幅不抠图；整幅=恒铺满、透明=不足留白、裁剪默认开、不裁剪=主体延伸出边框外沿）\n"
        "「收集册」打开我的收集册网页 · 「我的卡牌」文本列表\n"
        "（组合规则：带「整幅」忽略「主体悬浮」「描边」；「整幅+抠图」忽略「背景」，悬浮/描边保留）\n"
        "「销毁卡牌 卡名」销毁（无补偿）· 「上架卡牌 卡名 价格」出售\n"
        "「购买卡牌 卡名」买别人的卡 · 「卡牌市场」商店网页（含素材分页预览）\n"
        "「导出卡牌 卡名 为图片 / 为网页」导出卡牌文件（2D 图 / 3D 单文件网页）\n"
        "「素材包」查看素材 · 「购买背景 名称」「购买辉光 名称」等解锁素材（永久使用）\n"
        "同一用户不能做两张同名卡（会拦截，先销毁旧的或换名）；操作重名卡时用消息里给的卡牌 ID 指定")


@register(keywords=["卡牌制作"], help="🃏 制作卡牌（引用图片 + 卡名 [参数]）", role=ROLE_ALL)
async def cmd_card_make(ctx):
    urls = _deep_image_urls(ctx.message)
    if not urls:
        return await ctx.reply("请先引用/回复一张图片，再发「卡牌制作 卡名」喵")
    ok, why = forge.cardforge_ready()
    if not ok:
        return await ctx.reply(why)
    name, params = _split_name_params(ctx.args)
    if not name:
        return await ctx.reply("缺少卡牌名称，格式：卡牌制作 卡名 [参数]，如「卡牌制作 莲之空」")
    if len(name) > 20:
        return await ctx.reply("卡牌名称太长了喵（最多 20 字）")
    if cd.has_card_named(ctx.openid, name):
        return await ctx.reply(f"你已经有一张「{name}」了喵，换个名字，或先「销毁卡牌 {name}」再做新的")
    if forge.has_queued(ctx.openid, name):
        return await ctx.reply(f"「{name}」正在制作队列里喵，等它完成或换个名字")
    p = _parse_params(params)

    # 样式：默认整幅判定（恒铺满）；「透明」=透明主体判定（不足留白）；「整幅」显式整幅
    style = "transparent" if "透明" in p else "full-bleed"
    if "整幅" in p:
        style = "full-bleed"
    # 抠图是附加项：带「抠图/扣图」才抠出主体；「不抠图/不扣图」显式关闭（默认不抠）；
    # 「扣图/不扣图」是「抠图/不抠图」的同音容错写法
    no_matting = ("抠图" not in p and "扣图" not in p) or ("不抠图" in p or "不扣图" in p)
    skip_judge = "跳过评估" in p
    float_fg = "主体悬浮" in p
    outline = "描边" in p
    no_mask = "不裁剪" in p  # 「不裁剪」=主体可延伸出边框外沿（默认裁剪在边框内）

    adaptive = None
    if p.get("自适应") is not None:
        try:
            adaptive = int(p["自适应"])
        except (TypeError, ValueError):
            return await ctx.reply(f"自适应值「{p['自适应']}」不是数字喵")
        if adaptive not in (0, 1, 2):
            return await ctx.reply("自适应只支持 0/1/2：0=拉伸填满，1=整幅铺满，2=不足留白")

    glow_strength = None
    if p.get("辉光强度") is not None:
        try:
            glow_strength = float(p["辉光强度"])
        except (TypeError, ValueError):
            return await ctx.reply(f"辉光强度「{p['辉光强度']}」不是数字喵")
        if not 0 <= glow_strength <= 2:
            return await ctx.reply("辉光强度范围 0~2 喵")

    scale = None
    if p.get("缩放"):
        try:
            scale = float(p["缩放"])
            if not 0.5 <= scale <= 1.5:
                return await ctx.reply("缩放范围 0.5~1.5 喵")
        except ValueError:
            return await ctx.reply(f"缩放值「{p['缩放']}」不是数字喵")

    text_type = _TEXT_TYPE_MAP.get(p.get("卡牌类型") or "")
    if text_type is None:
        for kw, tt in _TEXT_KW.items():
            if kw in p:
                text_type = tt
                break
    if text_type is None and p.get("卡牌类型"):
        return await ctx.reply("卡牌类型只支持：文本 / 特殊文本 / 无文本")
    if text_type is None and (p.get("标题") or p.get("描述")):
        text_type = "transparent"  # 给了文字但没指定类型 → 特殊文本型

    title, desc = (p.get("标题") or "").strip(), (p.get("描述") or "").strip()
    need_text = text_type in ("boxed", "transparent") and (not title or not desc)

    materials = {}
    for cat_cn, (cat_key, _cn) in MATERIAL_CATS.items():
        val = p.get(cat_cn)
        if val is None:
            continue
        if cat_cn == "边框特效" and val in ("无", "none", "无特效"):
            materials["glow"] = None
            continue
        canon, err = _resolve_material(cat_key, val, ctx.openid)
        if err:
            return await ctx.reply(err)
        materials[cat_key] = canon

    # 组合规则：带「整幅」时忽略「主体悬浮」和「描边」；
    # 「整幅 + 抠图」时忽略「背景」（用原图做底，不另收背景使用费），
    # 同时不再忽略「主体悬浮」和「描边」
    if style == "full-bleed":
        if no_matting:
            float_fg = False
            outline = False
        else:
            materials["background"] = None

    base = _make_cost()
    fee = _use_fee()
    used = len([v for v in materials.values() if v])
    total = base + fee * used
    cd_min = _cooldown_min()
    if cd_min > 0:
        remain = cd_min * 60 - (int(time.time()) - cd.get_cooldown(ctx.openid))
        if remain > 0:
            mins = remain // 60 + (1 if remain % 60 else 0)
            return await ctx.reply(f"卡牌制作冷却中，还剩 {mins} 分钟喵，先钓钓鱼吧")
    if wallet.balance(ctx.openid) < total:
        extra = f"（基础 {base} + 素材使用费 {fee}×{used}）" if used else f"（制作费 {base}）"
        return await ctx.reply(f"喵币不足，需要 {total} 喵币{extra}，先钓鱼赚点喵币再来喵")

    if not wallet.spend(ctx.openid, total):
        return await ctx.reply("扣款失败，请重试")

    card_key = cd.new_card_key()
    task = {
        "sender": ctx.sender, "message": ctx.message, "openid": ctx.openid,
        "nick": _nick_of(ctx.message) or "神秘玩家",
        "image_url": urls[0], "name": name, "card_key": card_key, "cost": total,
        "need_text": need_text, "title": title, "desc": desc, "text_type": text_type,
        "style": style, "scale": scale, "no_matting": no_matting, "skip_judge": skip_judge,
        "float_fg": float_fg, "outline": outline, "no_mask": no_mask,
        "adaptive": adaptive, "glow_strength": glow_strength,
        "background": materials.get("background"), "frame": materials.get("frame"),
        "seal": materials.get("seal"), "back": materials.get("back"),
        "glow": materials.get("glow"),
    }
    okq, hint = await forge.submit(task)
    if not okq:
        wallet.add(ctx.openid, total)
        return await ctx.reply(hint)
    cd.set_cooldown(ctx.openid)
    await ctx.reply(hint)


@register(keywords=["收集册"], help="🃏 打开我的卡牌收集册网页", role=ROLE_ALL)
async def cmd_album(ctx):
    rec = cd.album(ctx.openid, _nick_of(ctx.message))
    n = cd.card_count(ctx.openid)
    await ctx.reply(f"📖 我的收集册（共 {n} 张）\n{STATIC_PUBLIC_URL}/album/{rec['key']}")


@register(keywords=["我的卡牌"], help="🃏 文本列出我持有的卡牌", role=ROLE_ALL)
async def cmd_my_cards(ctx):
    cards = cd.list_owned(ctx.openid)
    if not cards:
        return await ctx.reply("还没有卡牌喵，引用图片发「卡牌制作 卡名」做一张吧")
    lines = [f"🃏 我的卡牌（共 {len(cards)} 张）"]
    for i, c in enumerate(cards, 1):
        tag = cd.RARITY_EMOJI.get(c["rarity"], "")
        suffix = (f"　【市场 {cd.market_price_of(c['cardKey'])} 喵币】"
                  if c.get("status") == "market" else "")
        lines.append(f"{i}. {tag}{c['rarity']}·{c['name']}（{c['price']} 喵币）{suffix}")
    await ctx.reply("\n".join(lines))


@register(keywords=["销毁卡牌"], help="🃏 销毁卡牌（无补偿）", role=ROLE_ALL)
async def cmd_destroy_card(ctx):
    c, hint = _resolve_own_card(ctx.openid, ctx.args)
    if not c:
        return await ctx.reply(hint)
    if c.get("status") == "market":
        return await ctx.reply("这张卡正在市场出售，先「下架卡牌」再销毁喵")
    cd.remove_card(c["cardKey"])
    await ctx.reply(f"卡牌「{c['name']}」已销毁，无补偿喵")


@register(keywords=["卡牌市场"], help="🃏 卡牌商店网页（市场 + 素材分页预览）", role=ROLE_ALL)
async def cmd_card_market(ctx):
    n = len(cd.list_market())
    await ctx.reply(f"🏪 卡牌商店（市场 {n} 个挂单）\n{STATIC_PUBLIC_URL}/market\n"
                    "网页含素材分页（背景/边框/卡封/牌背/辉光）可预览\n"
                    "购买/上架/下架都在 QQ 里发文字命令：购买卡牌 卡名 / 上架卡牌 卡名 价格 / "
                    "下架卡牌 卡名 / 购买背景 名称 / 购买辉光 名称")


@register(keywords=["上架卡牌"], help="🃏 上架卡牌到市场出售", role=ROLE_ALL)
async def cmd_list_card(ctx):
    parts = ctx.args.rsplit(None, 1)
    if len(parts) == 2 and parts[1].isdigit():
        name, price = parts[0], int(parts[1])
    else:
        name, price = ctx.args.strip(), None
    if not name:
        return await ctx.reply("格式：上架卡牌 卡名 价格，如「上架卡牌 莲之空 800」")
    if price is None or price < 50:
        return await ctx.reply("价格至少 50 喵币，格式：上架卡牌 卡名 价格")
    c, hint = _resolve_own_card(ctx.openid, name)
    if not c:
        return await ctx.reply(hint)
    if c.get("status") == "market":
        return await ctx.reply("这张卡已经在市场里了喵")
    cd.place_order(c["cardKey"], price)
    await ctx.reply(f"卡牌「{c['name']}」已上架，售价 {price} 喵币\n{STATIC_PUBLIC_URL}/market")


@register(keywords=["下架卡牌"], help="🃏 撤回自己的市场挂单", role=ROLE_ALL)
async def cmd_unlist_card(ctx):
    c, hint = _resolve_own_card(ctx.openid, ctx.args)
    if not c:
        return await ctx.reply(hint)
    if c.get("status") != "market":
        return await ctx.reply("这张卡不在市场里喵")
    cd.cancel_order(c["cardKey"], ctx.openid)
    await ctx.reply(f"卡牌「{c['name']}」已下架，回到收集册喵")


@register(keywords=["购买卡牌"], help="🃏 从市场购买卡牌", role=ROLE_ALL)
async def cmd_buy_card(ctx):
    c, hint = _resolve_market_card(ctx.args)
    if not c:
        return await ctx.reply(hint)
    price = cd.market_price_of(c["cardKey"])
    tax = _tax()
    ok, msg = cd.buy_card(c["cardKey"], ctx.openid, tax)
    if not ok:
        return await ctx.reply(msg)
    rec = cd.album(ctx.openid)
    await ctx.reply(
        f"🎉 购买成功！「{c['name']}」已进入你的收集册（花费 {price} 喵币，卖家到手 "
        f"{round(price * (1 - tax))}，{round(price * tax)} 税回收）\n"
        f"{STATIC_PUBLIC_URL}/album/{rec['key']}")


@register(keywords=["素材包"], help="🎨 查看卡牌素材包清单", role=ROLE_ALL)
async def cmd_materials(ctx):
    prices = _prices()
    owned = cd.own_materials(ctx.openid)
    lines = ["🎨 素材包（购买后永久使用；sample-1 为基础素材默认可用）"]
    for cat_cn, (cat_key, cn) in MATERIAL_CATS.items():
        if cat_key == "glow":
            total = len(forge.GLOW_BUILTINS)
            base = 0
        else:
            names = forge.list_materials(cat_key)
            total = len(names)
            base = sum(1 for f in names if _is_free(cat_key, f))
        got = len(owned.get(cat_key, []))
        base_txt = f"（含基础素材 {base} 种）" if base else ""
        lines.append(f"· {cn}：{total} 种{base_txt}，{prices.get(cat_key, 0)} 喵币/个（已解锁 {got}）")
    lines.append("购买：购买背景 名称 / 购买边框 名称 / 购买卡封 名称 / 购买牌背 名称 / 购买辉光 名称")
    await ctx.reply("\n".join(lines))


@register(keywords=["购买素材", "购买背景", "购买边框", "购买牌背", "购买卡封", "购买辉光"],
          help="🎨 购买卡牌素材（永久解锁）", role=ROLE_ALL)
async def cmd_buy_material(ctx):
    kw = ctx.keyword or ""
    cat_key = _BUY_KW_CAT.get(kw)
    if cat_key is not None:
        # 快捷格式：「购买背景 星空」/「购买辉光 暖金描边」
        name = (ctx.args or "").strip()
        if not name:
            return await ctx.reply(f"格式：{kw} <名称>，发「素材包」或商店网页看清单")
    else:
        # 通用格式：「购买素材 背景 星空」
        parts = (ctx.args or "").strip().split(None, 1)
        if len(parts) != 2:
            return await ctx.reply("格式：购买素材 <类型> <名称>，如「购买素材 背景 星空」；"
                                   "也可直接发「购买背景 名称」「购买辉光 名称」")
        cat_cn, name = parts[0].strip(), parts[1].strip()
        entry = MATERIAL_CATS.get(cat_cn)
        if entry is None:
            return await ctx.reply(f"类型只支持：{'/'.join(MATERIAL_CATS)}")
        cat_key = entry[0]

    if cat_key == "glow":
        if name not in forge.GLOW_BUILTINS:
            return await ctx.reply(f"边框特效不存在，可选：{'/'.join(forge.GLOW_BUILTINS)}")
        canon = name
    else:
        p = forge.material_path(cat_key, name)
        if not p:
            return await ctx.reply("素材不存在喵，发「素材包」或商店网页查看清单")
        canon = os.path.basename(p)
    if _is_free(cat_key, canon):
        return await ctx.reply(f"「{os.path.splitext(canon)[0]}」是基础素材，默认可用，无需购买喵")
    if cd.owns_material(ctx.openid, cat_key, canon):
        return await ctx.reply("这个素材你已经解锁了喵")
    price = _prices().get(cat_key, 0)
    if wallet.balance(ctx.openid) < price:
        return await ctx.reply(f"喵币不足（{_CAT_CN.get(cat_key)} {price} 喵币）")
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply("扣款失败，请重试")
    cd.add_material(ctx.openid, cat_key, canon)
    await ctx.reply(f"🎨 解锁成功！{_CAT_CN.get(cat_key)}「{canon}」（花费 {price} 喵币），永久使用喵")


# ---------- 导出卡牌（命令行调用 cardforge --export，发回文件） ----------

_EXPORT_KW = (("为网页", "html"), ("为图片", "image"))


@register(keywords=["导出卡牌"], help="🃏 导出卡牌为图片 / 网页单文件", role=ROLE_ALL)
async def cmd_export_card(ctx):
    text = (ctx.args or "").strip()
    etype = None
    name = text
    for marker, et in _EXPORT_KW:
        i = text.rfind(marker)
        if i >= 0:
            etype, name = et, text[:i].strip()
            break
    if not name:
        return await ctx.reply(
            "格式：导出卡牌 <卡名> 为图片 或 为网页，如「导出卡牌 莲之空 为图片」喵")
    c, hint = _resolve_own_card(ctx.openid, name)
    if not c:
        return await ctx.reply(hint)
    if etype is None:
        return await ctx.reply(
            f"卡牌「{c['name']}」要导出成什么喵？\n"
            f"· 导出卡牌 {c['name']} 为图片（2D 合成图）\n"
            f"· 导出卡牌 {c['name']} 为网页（3D 交互单文件，图片已内嵌，可离线打开）")

    card_key = c["cardKey"]
    ext = "png" if etype == "image" else "html"
    out = os.path.join(_TMP_DIR, f"{card_key}.{ext}")
    try:
        rc, out_text = await forge._run_forge(
            ["--export", card_key, "--export-type", etype, "--out", out])
    except Exception as e:
        return await ctx.reply("导出失败：%s" % e)
    if rc != 0 or not os.path.isfile(out):
        tail = (out_text or "").strip().splitlines()
        return await ctx.reply("导出失败：" + ((tail[-1] if tail else "cardforge 返回异常")[:120]))

    if etype == "image":
        try:
            return await ctx.sender.send_image_with_text(
                ctx.message,
                f"🎴 卡牌「{c['name']}」（{cd.RARITY_EMOJI.get(c['rarity'], '')}{c['rarity']}）2D 图",
                out, reply=True)
        except Exception as e:
            return await ctx.reply("图片发送失败：%s" % e)

    try:
        return await ctx.sender.send_local_file(ctx.message, 4, out, reply=True)
    except Exception as e:
        return await ctx.reply("文件发送失败：%s" % e)


# web 后台命令列表（卡牌模块整体开关）
CARD_CMD_NAMES = {
    "cmd_card_help", "cmd_card_make", "cmd_album", "cmd_my_cards",
    "cmd_destroy_card", "cmd_card_market", "cmd_list_card", "cmd_unlist_card",
    "cmd_buy_card", "cmd_materials", "cmd_buy_material", "cmd_export_card",
}
