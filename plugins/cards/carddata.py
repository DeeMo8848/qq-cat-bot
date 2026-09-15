# -*- coding: utf-8 -*-
"""🃏 卡牌数据层：data/cards/ 持久化、userKey/cardKey 生成、收集册/市场/素材持有记录。

数据文件 data/cards/data.json 结构：
{
  "albums":    {openid: {"key": userKey, "nick": 昵称}},
  "cards":     {cardKey: 卡牌记录},
  "market":    {cardKey: {"seller": openid, "price": 价格, "listed_at": 时间}},
  "materials": {openid: {素材类型: [素材名...]}}
}
卡牌网页资产放在 data/cards/assets/<cardKey>/（由静态服务器白名单路由提供）。
"""

import hashlib
import hmac
import json
import os
import random
import secrets
import shutil
import threading
import time

from config import ROOT
from bot.core import wallet

_DATA_DIR = os.path.join(ROOT, "data", "cards")
_DATA_FILE = os.path.join(_DATA_DIR, "data.json")
_ASSETS_DIR = os.path.join(_DATA_DIR, "assets")
_SECRET_FILE = os.path.join(_DATA_DIR, "key_secret.txt")
os.makedirs(_DATA_DIR, exist_ok=True)
os.makedirs(_ASSETS_DIR, exist_ok=True)
_lock = threading.Lock()

# 品级 → (最低价, 最高价) 喵币区间；AI 只判品级，价格在区间内随机（取整到 50）
# 可在 settings.json 的 CARD_RARITY_RANGES 覆盖（Web 后台可改）
RARITY_RANGES = {"铜": (100, 300), "银": (500, 1200), "金": (2000, 5000), "虹": (8000, 20000)}
RARITY_EMOJI = {"铜": "🥉", "银": "🥈", "金": "🥇", "虹": "🌈"}


def _rarity_ranges() -> dict:
    try:
        with open(os.path.join(ROOT, "settings.json"), "r", encoding="utf-8") as f:
            ov = (json.load(f).get("CARD_RARITY_RANGES") or {})
        out = {k: (int(v[0]), int(v[1])) for k, v in ov.items()
               if isinstance(v, (list, tuple)) and len(v) >= 2}
        if out:
            return out
    except Exception:
        pass
    return dict(RARITY_RANGES)


def _load() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {"albums": {}, "cards": {}, "market": {}, "materials": {}}


def _save(data: dict):
    tmp = _DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _DATA_FILE)


def _secret() -> str:
    """链接签名密钥：settings.json 的 CARD_KEY_SECRET 优先，否则首次生成后持久化。"""
    try:
        with open(os.path.join(ROOT, "settings.json"), "r", encoding="utf-8") as f:
            s = (json.load(f).get("CARD_KEY_SECRET") or "").strip()
        if s:
            return s
    except Exception:
        pass
    if os.path.exists(_SECRET_FILE):
        try:
            s = open(_SECRET_FILE, "r", encoding="utf-8").read().strip()
            if s:
                return s
        except Exception:
            pass
    s = secrets.token_hex(16)
    with open(_SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(s)
    return s


def user_key(openid) -> str:
    """收集册链接标识：HMAC-SHA256(openid, secret) 截断，不可猜防遍历。"""
    return hmac.new(_secret().encode(), str(openid).encode(), hashlib.sha256).hexdigest()[:16]


def new_card_key() -> str:
    return secrets.token_hex(8)


def rand_price(rarity: str) -> int:
    lo, hi = _rarity_ranges().get(rarity, RARITY_RANGES["铜"])
    return random.randrange(lo, hi + 1) // 50 * 50


# ---------- 收集册 ----------

def album(openid, nick="") -> dict:
    """取/建收集册记录 {key, nick}。"""
    with _lock:
        data = _load()
        oid = str(openid)
        rec = data["albums"].setdefault(oid, {"key": user_key(oid), "nick": ""})
        if nick and rec.get("nick") != nick:
            rec["nick"] = nick[:20]
        _save(data)
        return dict(rec)


def openid_by_key(ukey: str):
    """由收集册 userKey 反查 openid。"""
    with _lock:
        data = _load()
        for oid, rec in data["albums"].items():
            if rec.get("key") == ukey:
                return oid
    return None


def nick_of(openid) -> str:
    with _lock:
        data = _load()
        return data["albums"].get(str(openid), {}).get("nick", "")


# ---------- 卡牌记录 ----------

def add_card(card: dict):
    with _lock:
        data = _load()
        data["cards"][card["cardKey"]] = card
        _save(data)


def get_card(card_key):
    with _lock:
        data = _load()
        c = data["cards"].get(card_key)
        return dict(c) if c else None


def list_owned(openid) -> list:
    """用户收集册内全部卡牌（含挂市场中）。"""
    with _lock:
        data = _load()
        oid = str(openid)
        return [dict(c) for c in data["cards"].values()
                if c.get("owner") == oid and c.get("status") in ("owned", "market")]


def find_cards(openid, text) -> list:
    """按 cardKey / 卡名精确 / 名称包含 找用户的所有匹配卡（含重名，返回列表）。"""
    with _lock:
        data = _load()
        oid = str(openid)
        t = (text or "").strip()
        if not t:
            return []
        exact, fuzzy = [], []
        for c in data["cards"].values():
            if c.get("owner") != oid:
                continue
            if c["cardKey"] == t or c.get("slug") == t or c.get("name") == t:
                exact.append(dict(c))
            elif t in (c.get("name") or "") or (c.get("name") or "") in t:
                fuzzy.append(dict(c))
        return exact or fuzzy


def find_card(openid, text):
    """按 cardKey / 卡名精确 / 名称包含 找用户的一张卡（唯一命中才返回）。"""
    hits = find_cards(openid, text)
    return hits[0] if len(hits) == 1 else None


def has_card_named(openid, name) -> bool:
    """用户是否已有同名卡（含挂市场中，排除已销毁）。"""
    with _lock:
        data = _load()
        oid = str(openid)
        t = (name or "").strip()
        return any(c.get("owner") == oid and c.get("status") in ("owned", "market")
                   and c.get("name") == t for c in data["cards"].values())


def remove_card(card_key) -> bool:
    """删除卡牌记录与网页资产（无补偿）。已挂市场的单子一并取消。"""
    with _lock:
        data = _load()
        c = data["cards"].pop(card_key, None)
        data["market"].pop(card_key, None)
        if c:
            _save(data)
    if c:
        shutil.rmtree(os.path.join(_ASSETS_DIR, card_key), ignore_errors=True)
    return bool(c)


def card_count(openid) -> int:
    with _lock:
        data = _load()
        oid = str(openid)
        return sum(1 for c in data["cards"].values()
                   if c.get("owner") == oid and c.get("status") in ("owned", "market"))


# ---------- 市场 ----------

def market_price_of(card_key) -> int:
    with _lock:
        data = _load()
        return int(data["market"].get(card_key, {}).get("price", 0))


def list_market() -> list:
    """市场挂单列表（每项为卡牌记录 + market_price + seller_nick）。"""
    with _lock:
        data = _load()
        out = []
        for ck, o in data["market"].items():
            card = data["cards"].get(ck)
            if not card:
                continue
            rec = dict(card)
            rec["market_price"] = int(o.get("price", 0))
            rec["seller_nick"] = data["albums"].get(o.get("seller", ""), {}).get("nick", "")
            out.append(rec)
        out.sort(key=lambda r: -r["market_price"])
        return out


def find_market_cards(text) -> list:
    """在市场里按卡名/cardKey 找所有匹配卡（含重名，返回列表）。"""
    with _lock:
        data = _load()
        t = (text or "").strip()
        if not t:
            return []
        exact, fuzzy = [], []
        for ck in data["market"]:
            c = data["cards"].get(ck)
            if not c:
                continue
            if c["cardKey"] == t or c.get("name") == t:
                exact.append(dict(c))
            elif t in (c.get("name") or "") or (c.get("name") or "") in t:
                fuzzy.append(dict(c))
        return exact or fuzzy


def find_market_card(text):
    """在市场里按卡名/cardKey 找卡（唯一命中才返回）。"""
    hits = find_market_cards(text)
    return hits[0] if len(hits) == 1 else None


def place_order(card_key, price) -> bool:
    with _lock:
        data = _load()
        c = data["cards"].get(card_key)
        if not c or c.get("status") != "owned":
            return False
        c["status"] = "market"
        data["market"][card_key] = {"seller": c["owner"], "price": int(price),
                                    "listed_at": int(time.time())}
        _save(data)
        return True


def cancel_order(card_key, openid) -> bool:
    with _lock:
        data = _load()
        o = data["market"].get(card_key)
        c = data["cards"].get(card_key)
        if not o or not c or o.get("seller") != str(openid):
            return False
        data["market"].pop(card_key, None)
        c["status"] = "owned"
        _save(data)
        return True


def buy_card(card_key, buyer, tax=0.1):
    """购买：买家付全款，卖家即时到账 (1-tax)，税回收。返回 (成功, 信息)。"""
    with _lock:
        data = _load()
        o = data["market"].get(card_key)
        c = data["cards"].get(card_key)
        if not o or not c:
            return False, "该卡牌已不在市场"
        price = int(o["price"])
        seller = o["seller"]
        if str(buyer) == seller:
            return False, "不能购买自己上架的卡牌"
        if wallet.balance(buyer) < price:
            return False, "喵币不足"
        wallet.spend(buyer, price)
        wallet.add(seller, round(price * (1 - tax)))
        data["market"].pop(card_key, None)
        c["owner"] = str(buyer)
        c["status"] = "owned"
        c["bought_at"] = int(time.time())
        _save(data)
        return True, "ok"


# ---------- 素材持有（购买后永久使用） ----------

def own_materials(openid) -> dict:
    with _lock:
        data = _load()
        return dict(data["materials"].get(str(openid), {}))


def owns_material(openid, cat, name) -> bool:
    with _lock:
        data = _load()
        return name in data["materials"].get(str(openid), {}).get(cat, [])


def add_material(openid, cat, name):
    with _lock:
        data = _load()
        rec = data["materials"].setdefault(str(openid), {})
        rec.setdefault(cat, [])
        if name not in rec[cat]:
            rec[cat].append(name)
        _save(data)


# ---------- 制作冷却 ----------

def set_cooldown(openid):
    """记录用户最近一次制作时间（冷却用）。"""
    with _lock:
        data = _load()
        data.setdefault("cooldowns", {})[str(openid)] = int(time.time())
        _save(data)


def get_cooldown(openid) -> int:
    """用户最近一次制作时间戳（秒），0=从未制作过。"""
    with _lock:
        data = _load()
        return int(data.get("cooldowns", {}).get(str(openid), 0))
