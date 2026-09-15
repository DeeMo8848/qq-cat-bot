# -*- coding: utf-8 -*-
"""静态页面服务：只开放 bot/public_html/ 目录，不暴露项目其它路径。

卡牌网页（动态路由，模板共用一份、按 key 查数据，实现"一个网页每个玩家看到自己的内容"）：
          /card/<cardKey>          单卡预览页
          /album/<userKey>         收集册页（userKey = HMAC-SHA256(openid) 截断 16 位，不可猜防遍历）
          /market                  卡牌市场页（含素材分页）
          /assets/<cardKey>/<file> 卡牌资产（白名单：仅 data/cards/assets/<cardKey>/，key 校验防穿越）
          /mimg/<cat>/<file>       素材预览图（白名单：仅 cardforge assets/<子目录>/，cat 白名单防穿越）
          /api/card/<cardKey>      JSON：单卡数据
          /api/album/<userKey>     JSON：收集册卡牌列表
          /api/market              JSON：市场挂单列表
          /api/materials           JSON：素材包清单（含预览图 URL，按类型分组）
        """

import logging
import os
import re
from urllib.parse import unquote

from aiohttp import web

from config import ROOT, STATIC_PORT

_log = logging.getLogger("static")

PUBLIC_DIR = os.path.join(ROOT, "bot", "public_html")
CARDS_WEB_DIR = os.path.join(ROOT, "plugins", "cards", "web")
CARDS_ASSETS_DIR = os.path.join(ROOT, "data", "cards", "assets")

# cardKey / userKey 均为 16 位小写十六进制（secrets.token_hex(8) / hmac 截断）
_KEY_RE = re.compile(r"^[0-9a-f]{16}$")


def _safe_path(rel: str) -> str:
    """把 URL 相对路径安全映射到 PUBLIC_DIR 内；越界一律返回空串。"""
    rel = unquote(rel or "").lstrip("/")
    if not rel:
        rel = "index.html"
    base = os.path.realpath(PUBLIC_DIR)
    target = os.path.realpath(os.path.join(base, rel))
    if target != base and not target.startswith(base + os.sep):
        return ""
    return target


def _safe_join(base: str, rel: str) -> str:
    """把相对路径安全映射到 base 目录内；越界返回空串。"""
    rel = unquote(rel or "").lstrip("/")
    if not rel:
        return ""
    b = os.path.realpath(base)
    t = os.path.realpath(os.path.join(b, rel))
    if t != b and not t.startswith(b + os.sep):
        return ""
    return t


def _cards_asset_path(key: str, rest: str) -> str:
    if not _KEY_RE.match(key or ""):
        return ""
    return _safe_join(os.path.join(CARDS_ASSETS_DIR, key), rest)


# 素材类型 → cardforge assets 子目录（白名单，防穿越）
_CAT_SUB = {"background": "backgrounds", "frame": "frames",
            "seal": "seals", "back": "backs"}
_CAT_CN = {"background": "背景", "frame": "边框", "seal": "卡封",
           "back": "牌背", "glow": "边框特效"}


async def _serve(request: web.Request):
    path = _safe_path(request.match_info.get("path", ""))
    if not path:
        return web.Response(status=404, text="404 找不到喵")
    if os.path.isdir(path):
        path = os.path.join(path, "index.html")
    if not os.path.isfile(path):
        return web.Response(status=404, text="404 找不到喵")
    return web.FileResponse(path)


# ---------- 卡牌页面 ----------

def _card_view(c: dict) -> dict:
    """卡牌记录 → 网页视图（附加资产 URL 字段，隐藏内部 materials）。"""
    key = c.get("cardKey", "")
    base = f"/assets/{key}"
    v = dict(c)
    v["card_png"] = f"{base}/card.png"
    has_web = bool(key) and os.path.isfile(os.path.join(CARDS_ASSETS_DIR, key, "card.html"))
    v["has_webcard"] = has_web
    v["card_html"] = f"{base}/card.html" if has_web else ""
    v.pop("materials", None)
    return v


def _template(name: str):
    p = os.path.join(CARDS_WEB_DIR, name)
    return p if os.path.isfile(p) else ""


def _render_template(name: str):
    p = _template(name)
    if not p:
        return web.Response(status=404, text="404 找不到喵")
    return web.FileResponse(p)


async def _card_page(request: web.Request):
    return _render_template("card.html")


async def _album_page(request: web.Request):
    return _render_template("album.html")


async def _market_page(request: web.Request):
    return _render_template("market.html")


async def _card_asset(request: web.Request):
    key = request.match_info["key"]
    rest = request.match_info.get("rest", "")
    path = _cards_asset_path(key, rest)
    if not path or not os.path.isfile(path):
        return web.Response(status=404, text="404 找不到喵")
    return web.FileResponse(path)


async def _material_img(request: web.Request):
    """素材预览图：白名单 cat + 安全路径，防止穿越到 cardforge 其它目录。"""
    cat = request.match_info["cat"]
    sub = _CAT_SUB.get(cat)
    if not sub:
        return web.Response(status=404, text="404 找不到喵")
    from plugins.cards import forge
    base = os.path.join(forge.CARDFORGE_DIR, "assets", sub)
    path = _safe_join(base, request.match_info["file"])
    if not path or not os.path.isfile(path):
        return web.Response(status=404, text="404 找不到喵")
    return web.FileResponse(path)


# ---------- 卡牌 API ----------

async def _api_card(request: web.Request):
    from plugins.cards import carddata as cd
    c = cd.get_card(request.match_info["key"])
    if not c:
        return web.json_response({"ok": False, "msg": "not found"})
    return web.json_response({"ok": True, "card": _card_view(c)})


async def _api_album(request: web.Request):
    from plugins.cards import carddata as cd
    oid = cd.openid_by_key(request.match_info["key"])
    if not oid:
        return web.json_response({"ok": False, "msg": "not found"})
    cards = cd.list_owned(oid)
    return web.json_response({"ok": True, "cards": [_card_view(c) for c in cards]})


async def _api_market(request: web.Request):
    from plugins.cards import carddata as cd
    cards = cd.list_market()
    return web.json_response({"ok": True, "cards": [_card_view(c) for c in cards]})


async def _api_materials(request: web.Request):
    """素材包清单：按类型分组，含预览图 URL 与单价；glow 为内置辉光（无图）。
    sample-1 基础素材标记 free=True，前端显示「基础素材」不标价。"""
    from plugins.cards import forge
    from plugins.cards.commands import _prices, FREE_MATERIALS
    prices = _prices()
    cats = []
    for cat in ("background", "frame", "seal", "back"):
        items = []
        for f in forge.list_materials(cat):
            stem = os.path.splitext(f)[0]
            items.append({"name": stem, "img": f"/mimg/{cat}/{f}",
                          "free": stem in FREE_MATERIALS})
        cats.append({"cat": cat, "cn": _CAT_CN.get(cat, cat),
                     "price": prices.get(cat, 0), "items": items})
    glow = [{"name": n, "img": "", "free": False} for n in forge.GLOW_BUILTINS]
    cats.append({"cat": "glow", "cn": _CAT_CN["glow"],
                 "price": prices.get("glow", 0), "items": glow})
    return web.json_response({"ok": True, "cats": cats})


async def start_static(port: int = STATIC_PORT):
    """启动静态页面服务，返回 (runner, site)；目录不存在时自动创建。"""
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    app = web.Application()
    # 卡牌动态路由（须先于兜底路由注册）
    app.router.add_get("/card/{key}", _card_page)
    app.router.add_get("/album/{key}", _album_page)
    app.router.add_get("/market", _market_page)
    app.router.add_get("/assets/{key}/{rest:.*}", _card_asset)
    app.router.add_get("/mimg/{cat}/{file}", _material_img)
    app.router.add_get("/api/card/{key}", _api_card)
    app.router.add_get("/api/album/{key}", _api_album)
    app.router.add_get("/api/market", _api_market)
    app.router.add_get("/api/materials", _api_materials)
    app.router.add_get("/{path:.*}", _serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[静态] 页面服务已启动: http://127.0.0.1:{port} （仅开放目录: {PUBLIC_DIR}）")
    return runner, site
