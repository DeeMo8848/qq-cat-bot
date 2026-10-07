# -*- coding: utf-8 -*-
"""影之诗制卡器 bot 插件 —— 命令实现。

两种合成方式（互斥触发，靠 matcher 区分，**不依赖注册顺序**）：

* **单卡图**：`sv卡牌` / `sv` / `szb卡牌` / `影之诗` / `影之诗卡牌`
* **效果图**：`sv卡牌效果图` / `sv效果图` / `szb卡牌效果图` / `影之诗效果图` / `影之诗卡牌效果图`

用户**必须引用一张图片**；其余参数全部可选，缺省走 config_schema 里的默认值。

用带 `matcher` 的命令注册（dispatch 会先遍历 matcher 组），因此**不会被原有的
「制作卡牌 / 卡牌制作」等子串命令抢走**。
"""

import asyncio
import io
import os
import uuid

import aiohttp
from PIL import Image

from config import ROOT
from bot.commands import register, ROLE_ALL

from . import config_schema as CS
from . import parser as P
from . import render as R

# 供 Web 后台「插件总开关」使用的命令名集合
SV_CMD_NAMES = {"cmd_sv_card", "cmd_sv_card_diy"}

# ---- 触发词 ----
WB_TRIGGERS = ["sv卡牌", "sv", "szb卡牌", "影之诗卡牌", "影之诗"]
DIY_TRIGGERS = ["sv卡牌效果图", "sv效果图", "szb卡牌效果图",
                "影之诗卡牌效果图", "影之诗效果图"]

_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}
_MAX_IMG = 10 * 1024 * 1024

_MAKE_TIMEOUT = 120
_TMP = os.path.join(ROOT, "tmp", "sv_card")


# ---------- 触发判定 ----------
def _hit(text: str, triggers) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in triggers)


def _matcher_diy(text: str) -> bool:
    return _hit(text, DIY_TRIGGERS)


def _matcher_wb(text: str) -> bool:
    # 含「效果图」的一律让给效果图命令，避免 "sv卡牌效果图" 被 "sv卡牌" 抢走
    if _hit(text, DIY_TRIGGERS):
        return False
    return _hit(text, WB_TRIGGERS)


# ---------- 图片获取 ----------
def _collect_image_urls(message):
    """收集消息里的图片 URL（引用图 / 直接发的图都覆盖）。

    优先用 webhook 适配对象上现成的 image_urls；否则回退到 attachments
    与嵌套 msg_elements（引用消息的结构会深一层）。
    """
    urls = list(getattr(message, "image_urls", None) or [])
    if urls:
        return urls

    def walk(elements):
        for el in elements or []:
            if not isinstance(el, dict):
                continue
            yield el
            yield from walk(el.get("msg_elements"))

    for att in getattr(message, "attachments", None) or []:
        u = att.get("url") if isinstance(att, dict) else getattr(att, "url", None)
        if u:
            urls.append(u)
    for el in walk(getattr(message, "msg_elements", None)):
        for att in el.get("attachments") or []:
            u = att.get("url") if isinstance(att, dict) else getattr(att, "url", None)
            if u:
                urls.append(u)
    # 去重保序
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _save_png_sync(content: bytes, path: str):
    """统一转 PNG —— wasm 渲染内核只接受 PNG 立绘。"""
    with Image.open(io.BytesIO(content)) as img:
        img.convert("RGBA").save(path, format="PNG")


async def _download_as_png(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=_HDRS) as session:
        async with session.get(url, timeout=timeout, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError("下载失败 HTTP %d" % resp.status)
            content = await resp.read()
    if len(content) > _MAX_IMG:
        raise RuntimeError("图片超过 10MB")
    os.makedirs(_TMP, exist_ok=True)
    path = os.path.join(_TMP, uuid.uuid4().hex + ".png")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save_png_sync, content, path)
    return path


# ---------- 配置组装 ----------
def _build_wb_config(params: dict) -> dict:
    """把单卡图参数组装成原工具的 CardConfig。"""
    kind = params.get("kind", 1)
    rarity = params.get("rarity", 4)
    special = params.get("special", "")
    cfg = {
        "name": params.get("name", ""),
        "language": "chs",
        "class": params.get("class", 0),
        "kind": kind,
        "rarity": rarity,
        "frame": CS.build_frame(kind, rarity, special),
        "cost": params.get("cost", ""),
        "atk": params.get("atk", ""),
        "life": params.get("life", ""),
        "style": "wb",
        "scale": 1.0,
    }
    return cfg


def _build_diy_config(params: dict) -> dict:
    """把效果图参数组装成原工具的 CardConfig（diy 风格）。"""
    kind = params.get("kind", 1)
    rarity = params.get("rarity", 4)
    special = params.get("special", "")
    cls = params.get("class", 0)
    f = CS.DIY_SIZE_FACTOR
    cfg = {
        "name": params.get("name", ""),
        "language": "chs",
        "class": cls,
        "kind": kind,
        "rarity": rarity,
        "frame": CS.build_frame(kind, rarity, special),
        "cost": params.get("cost", ""),
        "atk": params.get("atk", ""),
        "life": params.get("life", ""),
        "style": "diy",
        "scale": 1.0,
        # --- DIY 专属 ---
        "bg_type": CS.DIY_BG_TYPE,
        "trait_text": params.get("trait_text", ""),
        "class_title": "职业",
        "type_title": "类型",
        "class_text": CS.class_label(cls),
        "illus_title": "画师：",
        "d1_size": CS.DIY_SIZES["d1"] * f,
        "d2_size": CS.DIY_SIZES["d2"] * f,
        "ev_size": CS.DIY_SIZES["ev"] * f,
        "super_size": CS.DIY_SIZES["super"] * f,
        "crest_size": CS.DIY_SIZES["cre"] * f,
        "crests": [],
        "detail1": params.get("detail1", ""),
        "detail2": "",
        "show_detail2": False,
        "evolve": params.get("evolve", ""),
        "super_evolve": params.get("super_evolve", ""),
        "illustrator": params.get("illustrator", ""),
        "diy": params.get("diy", ""),
        "show_evolve": params.get("show_evolve", False),
        "show_super": params.get("show_super", False),
        "show_illustrator": params.get("show_illustrator", False),
        "show_diy": params.get("show_diy", True),
        "bg_alpha": CS.DIY_BG_ALPHA,
    }
    return cfg


# ---------- 通用主流程 ----------
async def _make(ctx, style: str, params: dict, build_cfg, what: str):
    art_path = out_path = None
    try:
        urls = _collect_image_urls(ctx.message)
        if not urls:
            await ctx.reply("请**引用一张图片**再发这条命令喵～\n"
                            "例：（引用图片）%s 卡名测试 龙族 虹 费10 攻5 体5"
                            % (WB_TRIGGERS[0] if style == "wb" else DIY_TRIGGERS[0]))
            return

        try:
            art_path = await _download_as_png(urls[0])
        except Exception as e:
            await ctx.reply("图片下载失败：%s" % e)
            return

        cfg = build_cfg(params)
        try:
            out_path = await R.render(cfg, style=style, art_path=art_path,
                                      timeout=_MAKE_TIMEOUT)
        except Exception as e:
            await ctx.reply("%s失败：%s" % (what, e))
            return

        await ctx.sender.send_local_file(ctx.message, 1, out_path, reply=False)
    finally:
        R.cleanup(art_path, out_path)


# ---------- 命令：单卡图 ----------
@register(
    keywords=WB_TRIGGERS,
    help="影之诗卡牌：引用图 +「sv卡牌/影之诗卡牌」等，可带卡名/职业/稀有度/费用等参数",
    matcher=_matcher_wb,
    role=ROLE_ALL,
)
async def cmd_sv_card(ctx):
    text = getattr(ctx, "args", None) or getattr(ctx.message, "content", "") or ""
    params, _unknown = P.parse_wb(text, WB_TRIGGERS)
    merged = P.merge_wb_defaults(params)
    await _make(ctx, "wb", merged, _build_wb_config, "制卡")


# ---------- 命令：效果图 ----------
@register(
    keywords=DIY_TRIGGERS,
    help="影之诗效果图：引用图 +「sv卡牌效果图」，支持正文/进化/超进化/画师/脚注",
    matcher=_matcher_diy,
    role=ROLE_ALL,
)
async def cmd_sv_card_diy(ctx):
    text = getattr(ctx, "args", None) or getattr(ctx.message, "content", "") or ""
    params, _unknown = P.parse_diy(text, DIY_TRIGGERS)
    merged = P.merge_diy_defaults(params)
    await _make(ctx, "diy", merged, _build_diy_config, "效果图制作")
