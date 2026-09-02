# -*- coding: utf-8 -*-
"""🐟 钓鱼游戏（复刻自 astrbot_plugin_fishing，改造为极简玩法）。
资金统一走 bot.core.wallet（喵喵币），持久化 data/fishing/fishing_data.json。
命令：开始钓鱼/我的鱼获/我的渔具箱/我的喵币/鱼具店/买鱼竿/买鱼钩/买鱼线/买鱼漂/
      买鱼饵/买高级鱼饵/附魔/洗附魔/卖鱼/一键卖鱼/排行榜/鱼获图鉴/扭蛋/钓鱼成就/
      鱼市/挂鱼/买鱼/撤单/交易所/持仓/转账/发红包/领红包/红包列表
"""

import json
import logging
import os
import random
import re
import threading
import time

from config import ROOT
from bot.commands import register, ROLE_ALL
from bot.core import wallet
from . import game

_log = logging.getLogger("fishing")

_DATA_DIR = os.path.join(ROOT, "data", "fishing")
_DATA_FILE = os.path.join(_DATA_DIR, "fishing_data.json")
os.makedirs(_DATA_DIR, exist_ok=True)
_lock = threading.Lock()

_RARITY_CN = game.RARITIES
_RARITY_EMOJI = {
    "common": "⚪", "fine": "🟢", "rare": "🔵", "epic": "🟣", "legend": "🔴",
}
_ROD_NAMES = {lv: name for lv, (name, _p) in game.RODS.items()}
_HOOK_NAMES = {lv: name for lv, (name, _p, _b) in game.HOOKS.items()}
_LINE_NAMES = {lv: name for lv, (name, _p, _m) in game.LINES.items()}
_FLOAT_NAMES = {lv: name for lv, (name, _p, _r) in game.FLOATS.items()}
_BAIT_NAMES = {k: v[0] for k, v in game.BAITS.items()}
GACHA_COST = 300          # 扭蛋单抽费用
TOTAL_SPECIES = len(game.FISH)
JACKPOT_REWARD = 20000    # 集齐全部鱼种的一次性大奖

# ---------- 附魔词条（MC 附魔台 + 二游词条风格） ----------
# 部位 -> [(key, 名称, 说明)]；附魔随机抽 1-3 个不重复词条，等级 1-3 随机
ENCHANT_POOL = {
    "rod": [
        ("fortune", "时运", "稀有度提升"),
        ("luck", "海之眷顾", "传说概率提升"),
        ("harvest", "丰收", "卖鱼收益提升"),
    ],
    "float": [
        ("lure", "饵钓", "上钩率提升"),
        ("double", "双钩", "概率一次钓两条"),
        ("treasure", "聚宝", "鱼价值提升"),
    ],
}
ENCHANT_INFO = {
    "fortune": ("时运", "稀有度提升"),
    "luck": ("海之眷顾", "传说概率提升"),
    "harvest": ("丰收", "卖鱼收益提升"),
    "lure": ("饵钓", "上钩率提升"),
    "double": ("双钩", "概率一次钓两条"),
    "treasure": ("聚宝", "鱼价值提升"),
}
ENCHANT_COST = 5000       # 随机附魔费用
UNENCHANT_COST = 1000     # 洗去附魔费用


def _load() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("users", {})
                    return data
        except Exception:
            _log.exception("读取钓鱼数据失败")
    return {"users": {}}


def _save(data: dict):
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _user(data, openid) -> dict:
    u = data["users"].setdefault(str(openid), {})
    u.setdefault("rod", 1)
    u.setdefault("hook", 1)
    u.setdefault("line", 1)
    u.setdefault("float", 1)
    u.setdefault("ench", {"rod": {}, "float": {}})
    u.setdefault("baits", {"bait1": 0, "bait2": 0})
    u.setdefault("inventory", {})
    u.setdefault("codex", {})
    u.setdefault("last_fish", 0)
    return u


def _stat(u):
    return u.setdefault("stats", {"catches": 0, "sold_earn": 0, "gacha": 0, "legend_seen": 0})


ACHIEVEMENTS = [
    ("ach_fish_1",   "初次上钩",   "累计钓到 1 条鱼",    lambda u, oid: _stat(u)["catches"] >= 1, 50),
    ("ach_fish_20",  "小有所获",   "累计钓到 20 条鱼",   lambda u, oid: _stat(u)["catches"] >= 20, 200),
    ("ach_fish_100", "渔场老手",   "累计钓到 100 条鱼",  lambda u, oid: _stat(u)["catches"] >= 100, 1000),
    ("ach_legend",   "传奇渔夫",   "钓到过 1 条传说鱼",  lambda u, oid: _stat(u)["legend_seen"] >= 1, 2000),
    ("ach_gacha_10", "扭蛋常客",   "累计扭蛋 10 次",     lambda u, oid: _stat(u)["gacha"] >= 10, 500),
    ("ach_gacha_50", "扭蛋上瘾",   "累计扭蛋 50 次",     lambda u, oid: _stat(u)["gacha"] >= 50, 2000),
    ("ach_sell_5k",  "第一桶金",   "累计卖鱼赚满 5000",  lambda u, oid: _stat(u)["sold_earn"] >= 5000, 500),
    ("ach_rich",     "小有资产",   "当前余额达 50000",   lambda u, oid: wallet.balance(oid) >= 50000, 3000),
]


def _check_achievements(u, oid):
    done = set(u.get("achievements", []))
    got = []
    for aid, name, _desc, met, rew in ACHIEVEMENTS:
        if aid in done:
            continue
        if met(u, oid):
            done.add(aid)
            u["achievements"] = sorted(done)
            wallet.add(oid, rew)
            got.append((name, rew))
    return got


def _record_catch(u, fish, oid):
    """入库 + 点亮图鉴，返回 {is_new, first_bonus, jackpot}。"""
    st = _stat(u)
    st["catches"] += 1
    if fish["rarity"] == "legend":
        st["legend_seen"] = 1
    u.setdefault("inventory", {}).setdefault(fish["id"], []).append(fish["weight_g"])
    codex = u.setdefault("codex", {})
    is_new = fish["id"] not in codex
    codex[fish["id"]] = codex.get(fish["id"], 0) + 1
    info = {"is_new": is_new, "first_bonus": 0, "jackpot": False}
    if is_new:
        base = game.FISH[fish["id"]][3]
        info["first_bonus"] = base * 4
        wallet.add(oid, info["first_bonus"])
    if len(codex) >= TOTAL_SPECIES and not u.get("codex_full"):
        u["codex_full"] = 1
        info["jackpot"] = True
        wallet.add(oid, JACKPOT_REWARD)
    return info


# ---------- 命令：开始钓鱼 ----------
@register(keywords=["开始钓鱼"], help="🐟 抛一竿钓条鱼喵", role=ROLE_ALL, exact=True)
async def cmd_fish(ctx):
    oid = ctx.openid
    now = int(time.time())
    with _lock:
        data = _load()
        u = _user(data, oid)
        cd = 45
        last = u.get("last_fish", 0)
        if now - last < cd:
            wait = cd - (now - last)
            return await ctx.reply_text(f"🐟 手都酸啦，休息 {wait} 秒再抛竿喵")
        has_bait = []
        if u["baits"].get("bait2", 0) > 0:
            u["baits"]["bait2"] -= 1
            has_bait = ["bait2"]
        elif u["baits"].get("bait1", 0) > 0:
            u["baits"]["bait1"] -= 1
            has_bait = ["bait1"]
        rod = int(u.get("rod", 1))
        hook = int(u.get("hook", 1))
        line = int(u.get("line", 1))
        flt = int(u.get("float", 1))
        ench = u.get("ench", {})
        rod_ench = ench.get("rod", {})
        float_ench = ench.get("float", {})
        fortune = rod_ench.get("fortune", 0)
        luck = rod_ench.get("luck", 0)
        lure = float_ench.get("lure", 0)
        double = float_ench.get("double", 0)
        treasure = float_ench.get("treasure", 0)
        if random.random() > game.hook_rate(flt, lure):
            u["last_fish"] = now
            _save(data)
            return await ctx.reply_text("🐟 鱼咬钩又跑啦…升级鱼漂或饵钓附魔能提高上钩率喵")
        fish = game.roll_fish(rod, has_bait, hook_lv=hook, line_lv=line,
                              fortune_lv=fortune, luck_lv=luck)
        if treasure:
            fish["value"] = int(fish["value"] * (1 + 0.1 * treasure))
        info = _record_catch(u, fish, oid)
        extra = None
        if double and random.random() < 0.08 * double:
            extra = game.roll_fish(rod, has_bait, hook_lv=hook, line_lv=line,
                                   fortune_lv=fortune, luck_lv=luck)
            if treasure:
                extra["value"] = int(extra["value"] * (1 + 0.1 * treasure))
            _record_catch(u, extra, oid)
        new_ach = _check_achievements(u, oid)
        u["last_fish"] = now
        _save(data)

    rod_name = _ROD_NAMES.get(rod, "手竿")
    now_txt = "🌙 夜钓" if game.is_night() else "☀️ 日钓"
    bait_txt = f"（用了「{_BAIT_NAMES[has_bait[0]]}」）" if has_bait else ""
    lines = [
        f"{now_txt} 你抛了「{rod_name}」{bait_txt}",
        f"🎣 钓到：{fish['emoji']} {fish['name']} 喵！",
        f"{_RARITY_EMOJI[fish['rarity']]} {_RARITY_CN[fish['rarity']]} · "
        f"重 {game.fmt_weight(fish['weight_g'])}",
        f"💰 约值 {fish['value']} 喵喵币（发「卖鱼 <数量> <鱼名>」或「一键卖鱼」可换钱）",
    ]
    if extra:
        lines.append(
            f"🎣 双钩发动！又钓到：{extra['emoji']} {extra['name']} "
            f"({_RARITY_EMOJI[extra['rarity']]}{_RARITY_CN[extra['rarity']]} · "
            f"约值 {extra['value']} 币)"
        )
    if info.get("is_new"):
        lines.append(f"📖 图鉴新收录「{fish['name']}」！+{info['first_bonus']} 喵币")
    if info.get("jackpot"):
        lines.append(f"🎉 集齐全部鱼种！大奖 +{JACKPOT_REWARD} 喵币！")
    lines += [f"🏅 达成成就「{n}」+{r}喵币！" for n, r in new_ach]
    return await ctx.reply_text("\n".join(lines))


@register(keywords=["我的鱼获", "鱼获"], help="🎒 查看钓到的鱼", role=ROLE_ALL, exact=True)
async def cmd_inventory(ctx):
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        inv = u["inventory"]
        rod = int(u.get("rod", 1))
    if not inv:
        return await ctx.reply_text(
            f"🎒 鱼获空空如也，先发「开始钓鱼」捞一竿喵！\n"
            f"当前钓竿：{_ROD_NAMES.get(rod, '?')}（鱼具店可升级）"
        )
    lines = []
    total = 0
    total_n = 0
    for fid, weights in inv.items():
        try:
            name, emoji, rr, base, _w, _z = game.FISH[fid]
        except KeyError:
            continue
        cnt = len(weights)
        total_n += cnt
        val = sum(base + int(w / 8) for w in weights)
        total += val
        lines.append(
            f"{emoji} {name} ×{cnt}  {_RARITY_EMOJI[rr]}{_RARITY_CN[rr]}  ≈{val}币"
        )
    lines.append(f"　合计：{total_n} 条 · 估值 {total} 喵喵币")
    return await ctx.reply_text(
        f"🎒 我的鱼获：\n" + "\n".join(lines) +
        f"\n发「卖鱼 <数量> <鱼名>」或「一键卖鱼」可换成喵喵币喵"
    )


@register(keywords=["我的喵币", "喵币"], help="💰 查询喵喵币余额",
          role=ROLE_ALL, exact=True)
async def cmd_balance(ctx):
    bal = wallet.balance(ctx.openid)
    return await ctx.reply_text(f"💰 你现有 **{bal}** {wallet.COIN}喵")


@register(keywords=["鱼具店", "渔具店"], help="🏪 钓竿与鱼饵价格表",
          role=ROLE_ALL, exact=True)
async def cmd_shop(ctx):
    def _gear_lines(gears):
        lines = []
        for lv in sorted(gears):
            name, price, *_ = gears[lv]
            src = "新手自带" if price == 0 else f"{price} 喵币"
            lines.append(f"· 第{lv}级 {name} — {src}")
        return lines
    rod_lines = _gear_lines(game.RODS)
    hook_lines = _gear_lines(game.HOOKS)
    line_lines = _gear_lines(game.LINES)
    float_lines = _gear_lines(game.FLOATS)
    bait_lines = []
    for k in ("bait1", "bait2"):
        name, price, bonus = game.BAITS[k]
        btxt = " / ".join(f"{_RARITY_CN[r]}+" for r in bonus)
        bait_lines.append(f"· {name} {price}币/个（{btxt}）")
    ench_lines = [
        f"· 附魔 <钓竿|鱼漂>：{ENCHANT_COST}币/次，随机 1-3 个词条（各 1-3 级）",
        f"· 洗附魔 <钓竿|鱼漂>：{UNENCHANT_COST}币，洗去该部位附魔",
        "· 钓竿词条：时运/海之眷顾/丰收 · 鱼漂词条：饵钓/双钩/聚宝",
    ]
    return await ctx.reply_text(
        "🏪 鱼具店：\n"
        "🎣 钓竿\n" + "\n".join(rod_lines) + "\n"
        "🪝 鱼钩（稀有度）\n" + "\n".join(hook_lines) + "\n"
        "🧵 鱼线（大鱼）\n" + "\n".join(line_lines) + "\n"
        "🎈 鱼漂（上钩率）\n" + "\n".join(float_lines) + "\n"
        "🪱 鱼饵\n" + "\n".join(bait_lines) + "\n"
        "✨ 附魔\n" + "\n".join(ench_lines) + "\n"
        "买法：买鱼竿 <1-5> · 买鱼钩 <2-6> · 买鱼线 <2-6> · 买鱼漂 <2-6> · "
        "买鱼饵 <数量> · 买高级鱼饵 <数量> · 附魔 <部位>"
    )


def _starts_with(*kws):
    kws = set(kws)
    return lambda text: any(text.startswith(k) for k in kws)


def _strip_cmd(ctx, *kws):
    """matcher 触发时 ctx.args 为整条消息（含命令词），剥掉命令词后返回参数。"""
    t = (ctx.args or "").strip()
    for kw in kws:
        if t.startswith(kw):
            return t[len(kw):].strip()
    return t


def _sender_name(ctx) -> str:
    """取发送者昵称（群聊昵称在消息作者 username 字段）。"""
    author = getattr(ctx.message, "author", None)
    if isinstance(author, dict):
        for k in ("username", "member_name", "user_name", "nickname"):
            v = author.get(k)
            if v:
                return str(v).strip()
        return ""
    try:
        name = getattr(author, "username", None) or getattr(author, "member_name", None) \
            or getattr(author, "user_name", None) or getattr(author, "nickname", None)
    except Exception:
        name = None
    return (name or "").strip()


@register(keywords=["买鱼竿"], help="", role=ROLE_ALL, matcher=_starts_with("买鱼竿"))
async def cmd_buy_rod(ctx):
    lv_txt = _strip_cmd(ctx, "买鱼竿")
    if lv_txt not in ("1", "2", "3", "4", "5"):
        return await ctx.reply_text("请发「买鱼竿 1-5」喵（1木 2竹 3碳素 4钛合金 5传说）")
    lv = int(lv_txt)
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        cur = int(u.get("rod", 1))
        if lv <= cur:
            return await ctx.reply_text(
                f"你现在已是「{_ROD_NAMES.get(cur, '?')}」啦，无需降级喵"
            )
    name, price = game.RODS[lv]
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply_text(
            f"💸 余额不足，买「{name}」需 {price} 喵喵币（先去钓鱼卖钱喵）"
        )
    with _lock:
        data = _load()
        _user(data, ctx.openid)["rod"] = lv
        _save(data)
    return await ctx.reply_text(
        f"🎣 已换成「{name}」！下次抛竿更容易出好鱼，冲鸭~"
    )


def _make_buy_gear(kw, attr, gears, cmd_name):
    """通用配件购买：买鱼钩/买鱼线/买鱼漂。"""
    @register(keywords=[kw], help="", role=ROLE_ALL, matcher=_starts_with(kw))
    async def _buy(ctx):
        lv_txt = _strip_cmd(ctx, kw)
        if lv_txt not in ("2", "3", "4", "5", "6"):
            return await ctx.reply_text(f"请发「{kw} <2-6>」喵（2-6 级）")
        lv = int(lv_txt)
        with _lock:
            data = _load()
            u = _user(data, ctx.openid)
            cur = int(u.get(attr, 1))
            if lv <= cur:
                return await ctx.reply_text(
                    f"你现在已是「{gears[cur][0]}」啦，无需降级喵"
                )
        name, price, *_ = gears[lv]
        if not wallet.spend(ctx.openid, price):
            return await ctx.reply_text(
                f"💸 余额不足，买「{name}」需 {price} 喵喵币（先去钓鱼卖钱喵）"
            )
        with _lock:
            data = _load()
            _user(data, ctx.openid)[attr] = lv
            _save(data)
        return await ctx.reply_text(f"✅ 已换上「{name}」！下次抛竿更给力喵~")
    _buy.__name__ = cmd_name
    return _buy


cmd_buy_hook = _make_buy_gear("买鱼钩", "hook", game.HOOKS, "cmd_buy_hook")
cmd_buy_line = _make_buy_gear("买鱼线", "line", game.LINES, "cmd_buy_line")
cmd_buy_float = _make_buy_gear("买鱼漂", "float", game.FLOATS, "cmd_buy_float")


@register(keywords=["我的渔具箱", "渔具箱"], help="🎒 查看全部渔具与附魔", role=ROLE_ALL, exact=True)
async def cmd_equip(ctx):
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        rod = int(u.get("rod", 1))
        hook = int(u.get("hook", 1))
        line = int(u.get("line", 1))
        flt = int(u.get("float", 1))
        ench = u.get("ench", {})
        baits = dict(u["baits"])
    lines = [
        f"🎣 钓竿：{_ROD_NAMES.get(rod, '?')}",
        f"🪝 鱼钩：{_HOOK_NAMES.get(hook, '?')}",
        f"🧵 鱼线：{_LINE_NAMES.get(line, '?')}",
        f"🎈 鱼漂：{_FLOAT_NAMES.get(flt, '?')}",
    ]
    ench_lines = []
    for part_cn, part in (("钓竿", "rod"), ("鱼漂", "float")):
        part_ench = ench.get(part, {})
        if part_ench:
            items = []
            for k, lv in sorted(part_ench.items()):
                name, _d = ENCHANT_INFO.get(k, (k, ""))
                items.append(f"{name} {game.ENCHANT_LV_CN[lv]}")
            ench_lines.append(f"{part_cn}：{' / '.join(items)}")
        else:
            ench_lines.append(f"{part_cn}：无")
    if any(ench.get(p) for p in ("rod", "float")):
        lines.append("✨ 附魔：" + " · ".join(ench_lines))
    else:
        lines.append("✨ 附魔：暂无（发「附魔 钓竿」随机获取）")
    lines.append(f"🪱 蚯蚓×{baits.get('bait1', 0)} · 高级鱼饵×{baits.get('bait2', 0)}")
    next_rod = rod + 1
    if next_rod in game.RODS:
        lines.append(f"🔜 下一把「{_ROD_NAMES[next_rod]}」需 {game.RODS[next_rod][1]} 喵币（发「买鱼竿 {next_rod}」升级）")
    else:
        lines.append("🔝 已经是最强钓竿了喵 ✨")
    return await ctx.reply_text("\n".join(lines))


@register(keywords=["附魔"], help="", role=ROLE_ALL, matcher=_starts_with("附魔"))
async def cmd_enchant(ctx):
    parts = _strip_cmd(ctx, "附魔").split()
    if len(parts) != 1:
        return await ctx.reply_text(
            "请发「附魔 <部位>」喵，例如：附魔 钓竿\n"
            "随机获得 1-3 个词条（各 1-3 级），不满意可「洗附魔 <部位>」重来"
        )
    part_cn = parts[0]
    part_map = {"钓竿": "rod", "鱼竿": "rod", "鱼漂": "float", "浮漂": "float"}
    part = part_map.get(part_cn)
    if part is None:
        return await ctx.reply_text("可附魔部位：钓竿、鱼漂喵")
    if not wallet.spend(ctx.openid, ENCHANT_COST):
        return await ctx.reply_text(
            f"💸 余额不足！附魔需 {ENCHANT_COST} 喵喵币喵"
        )
    pool = ENCHANT_POOL[part]
    n = random.randint(1, 3)
    keys = random.sample([k for k, _n, _d in pool], n)
    ench = {k: random.randint(1, 3) for k in keys}
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        u.setdefault("ench", {})[part] = ench
        _save(data)
    lines = [f"✨ {part_cn}附魔结果（{ENCHANT_COST}币）："]
    for k, lv in ench.items():
        name, desc = ENCHANT_INFO[k]
        lines.append(f"· {name} {game.ENCHANT_LV_CN[lv]}（{desc}）")
    lines.append("不满意？发「洗附魔 <部位>」洗掉重来喵")
    return await ctx.reply_text("\n".join(lines))


@register(keywords=["洗附魔"], help="", role=ROLE_ALL, matcher=_starts_with("洗附魔"))
async def cmd_unenchant(ctx):
    parts = _strip_cmd(ctx, "洗附魔").split()
    if len(parts) != 1:
        return await ctx.reply_text("请发「洗附魔 <部位>」喵，例如：洗附魔 钓竿")
    part_cn = parts[0]
    part_map = {"钓竿": "rod", "鱼竿": "rod", "鱼漂": "float", "浮漂": "float"}
    part = part_map.get(part_cn)
    if part is None:
        return await ctx.reply_text("可洗附魔部位：钓竿、鱼漂喵")
    if not wallet.spend(ctx.openid, UNENCHANT_COST):
        return await ctx.reply_text(
            f"💸 余额不足！洗附魔需 {UNENCHANT_COST} 喵喵币喵"
        )
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        u.setdefault("ench", {})[part] = {}
        _save(data)
    return await ctx.reply_text(
        f"🧹 已洗去「{part_cn}」的附魔，可以重新附魔啦喵"
    )


def _make_buy_bait(bait_key, kw, cmd_name):
    @register(keywords=[kw], help="", role=ROLE_ALL, matcher=_starts_with(kw))
    async def _buy(ctx):
        n_txt = _strip_cmd(ctx, kw)
        if not n_txt.isdigit() or int(n_txt) < 1:
            return await ctx.reply_text(f"请发「{kw} <数量>」喵，例如：{kw} 5")
        n = int(n_txt)
        name, price, _bonus = game.BAITS[bait_key]
        cost = price * n
        if not wallet.spend(ctx.openid, cost):
            return await ctx.reply_text(
                f"💸 余额不足，买 {n} 个「{name}」需 {cost} 喵喵币喵"
            )
        with _lock:
            data = _load()
            u = _user(data, ctx.openid)
            u["baits"][bait_key] = u["baits"].get(bait_key, 0) + n
            _save(data)
        return await ctx.reply_text(
            f"🪱 已购入 {n} 个「{name}」（共 {cost} 喵币），钓鱼时自动使用喵~"
        )
    _buy.__name__ = cmd_name
    return _buy


cmd_buy_bait1 = _make_buy_bait("bait1", "买鱼饵", "cmd_buy_bait1")
cmd_buy_bait2 = _make_buy_bait("bait2", "买高级鱼饵", "cmd_buy_bait2")


@register(keywords=["卖鱼"], help="", role=ROLE_ALL, matcher=_starts_with("卖鱼"))
async def cmd_sell(ctx):
    parts = _strip_cmd(ctx, "卖鱼").split()
    if len(parts) < 1:
        return await ctx.reply_text(
            "请发「卖鱼 <鱼名>」或「卖鱼 <数量> <鱼名>」喵，例如：卖鱼 3 鲤鱼"
        )
    qty = 1
    if parts[0].isdigit():
        qty = int(parts[0])
        name_parts = parts[1:]
    else:
        name_parts = parts
    if not name_parts:
        return await ctx.reply_text("要说明卖哪种鱼喵，例如：卖鱼 3 鲤鱼")
    fish_name = "".join(name_parts)

    target_id = None
    for fid, (name, _e, _rr, _b, _w, _z) in game.FISH.items():
        if name == fish_name or fish_name in name:
            target_id = fid
            break
    if target_id is None:
        return await ctx.reply_text(f"鱼店里没有「{fish_name}」这种鱼喵，发「我的鱼获」看看")

    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        inv = u["inventory"]
        mine = inv.get(target_id, [])
        if not mine:
            return await ctx.reply_text(f"你的背包里没有「{game.FISH[target_id][0]}」喵")
        if qty > len(mine):
            qty = len(mine)
        weights = mine[:qty]
        base = game.FISH[target_id][3]
        earned = sum(base + int(w / 8) for w in weights)
        harvest = u.get("ench", {}).get("rod", {}).get("harvest", 0)
        if harvest:
            earned = int(earned * (1 + 0.1 * harvest))
        del mine[:qty]
        if not mine:
            inv.pop(target_id, None)
        _stat(u)["sold_earn"] += earned
        new_ach = _check_achievements(u, ctx.openid)
        _save(data)

    wallet.add(ctx.openid, earned)
    name = game.FISH[target_id][0]
    lines = [
        f"💰 卖出 {name} ×{qty}，赚得 **{earned}** {wallet.COIN}喵！",
        f"当前余额：{wallet.balance(ctx.openid)} 喵喵币",
    ]
    lines += [f"🏅 达成成就「{n}」+{r}喵币！" for n, r in new_ach]
    return await ctx.reply_text("\n".join(lines))



@register(keywords=["一键卖鱼"], help="💰 一次卖出全部鱼获", role=ROLE_ALL, exact=True)
async def cmd_sell_all(ctx):
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        inv = u["inventory"]
        if not inv:
            return await ctx.reply_text("🎒 鱼获空空如也，先发「开始钓鱼」捞一竿喵！")
        harvest = u.get("ench", {}).get("rod", {}).get("harvest", 0)
        total_n = 0
        earned = 0
        detail = []
        for fid, weights in list(inv.items()):
            try:
                name, emoji, rr, base, _w, _z = game.FISH[fid]
            except KeyError:
                continue
            cnt = len(weights)
            val = sum(base + int(w / 8) for w in weights)
            if harvest:
                val = int(val * (1 + 0.1 * harvest))
            total_n += cnt
            earned += val
            detail.append(f"{emoji}{name} ×{cnt} ≈{val}币")
        u["inventory"] = {}
        _stat(u)["sold_earn"] += earned
        new_ach = _check_achievements(u, ctx.openid)
        _save(data)
    wallet.add(ctx.openid, earned)
    lines = [f"💰 一键卖出全部鱼获（{total_n} 条）！"]
    if len(detail) <= 15:
        lines.append("\n".join(detail))
    else:
        lines.append("\n".join(detail[:15]) + f"\n…等 {len(detail) - 15} 种")
    lines.append(f"💴 共赚得 **{earned}** {wallet.COIN}喵！")
    lines.append(f"当前余额：{wallet.balance(ctx.openid)} 喵喵币")
    lines += [f"🏅 达成成就「{n}」+{r}喵币！" for n, r in new_ach]
    return await ctx.reply_text("\n".join(lines))

@register(keywords=["排行榜"], help="🏆 喵喵币财富榜", role=ROLE_ALL, exact=True)
async def cmd_rank(ctx):
    items = wallet.top(10)
    if not items:
        return await ctx.reply_text("🏆 暂时还没有人有喵喵币，快去钓鱼挣第一桶金喵！")
    medals = ("🥇", "🥈", "🥉")
    lines = []
    for i, (_oid, bal) in enumerate(items):
        tag = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{tag} {bal} 喵币")
    return await ctx.reply_text("🏆 喵喵币财富榜 TOP" + str(len(items)) + "：\n" + "\n".join(lines))


# ---------- 命令：鱼获图鉴 ----------

@register(keywords=["鱼获图鉴", "钓鱼图鉴"], help="🐟 鱼种图鉴收集进度", role=ROLE_ALL, exact=True)
async def cmd_codex(ctx):
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        codex = u.get("codex", {})
    if not codex:
        return await ctx.reply_text(
            "🐟 图鉴空空如也，先钓鱼/扭蛋收集鱼种喵！\n"
            "· 开始钓鱼 — 解锁昼夜鱼池\n"
            "· 扭蛋 — 花 300 喵币抽（不限池，更快集齐）"
        )
    rarity_total = {}
    for fid, (_n, _e, rr, *_rest) in game.FISH.items():
        rarity_total[rr] = rarity_total.get(rr, 0) + 1
    lines = []
    for rr in game.RARITY_ORDER:
        have = sum(1 for fid in codex if game.FISH[fid][2] == rr)
        lines.append(f"{_RARITY_EMOJI[rr]} {_RARITY_CN[rr]}：{have}/{rarity_total.get(rr, 0)}")
    progress = len(codex)
    lines.append(f"\n📖 已解锁 {progress}/{TOTAL_SPECIES} 种")
    if progress >= TOTAL_SPECIES:
        lines.append("🎉 图鉴全收集！已解锁集齐大奖")
    else:
        lines.append(f"集齐全部可得 +{JACKPOT_REWARD} 喵币大奖喵")
    return await ctx.reply_text("🐟 鱼获图鉴：\n" + "\n".join(lines))


# ---------- 命令：扭蛋 ----------

@register(keywords=["扭蛋", "抽鱼"], help="🎰 花300喵币抽一条随机鱼", role=ROLE_ALL, exact=True)
async def cmd_gacha(ctx):
    oid = ctx.openid
    if not wallet.spend(oid, GACHA_COST):
        return await ctx.reply_text(f"💸 余额不足！扭蛋需 {GACHA_COST} 喵喵币（先去钓鱼卖钱喵）")
    fish = game.roll_gacha()
    with _lock:
        data = _load()
        u = _user(data, oid)
        _stat(u)["gacha"] += 1
        info = _record_catch(u, fish, oid)
        new_ach = _check_achievements(u, oid)
        _save(data)
    lines = [
        f"🎰 你花了 {GACHA_COST} 喵币，扭蛋转啊转…",
        f"{fish['emoji']} 抽到：{fish['name']}！",
        f"{_RARITY_EMOJI[fish['rarity']]} {_RARITY_CN[fish['rarity']]} · 重 {game.fmt_weight(fish['weight_g'])} · 值 {fish['value']} 喵币（可卖）",
    ]
    if info["is_new"]:
        lines.append(f"✨ 图鉴新纪录！首次发现奖励 +{info['first_bonus']} 喵币")
    if info["jackpot"]:
        lines.append(f"🏆🎉 集齐全部 {TOTAL_SPECIES} 种！大奖 +{JACKPOT_REWARD} 喵币！")
    return await ctx.reply_text("\n".join(lines))


# ---------- 命令：钓鱼成就 ----------

@register(keywords=["钓鱼成就"], help="🏅 查看钓鱼成就", role=ROLE_ALL, exact=True)
async def cmd_achievement(ctx):
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        stats = _stat(u)
        done = set(u.get("achievements", []))
    head = (f"🏅 钓鱼成就（已解锁 {len(done)}/{len(ACHIEVEMENTS)}）\n"
            f"📊 钓到 {stats['catches']} 条 · 卖鱼赚 {stats['sold_earn']} 喵币 · 扭蛋 {stats['gacha']} 次\n")
    lines = []
    for aid, name, desc, _met, rew in ACHIEVEMENTS:
        if aid in done:
            lines.append(f"✅ {name}（{desc}）已达成 +{rew}喵币")
        else:
            lines.append(f"🔒 {name}（{desc}）+{rew}喵币")
    return await ctx.reply_text(head + "\n".join(lines))


# ---------- 玩家交易市场（10% 交易税回收，抑制通胀） ----------

MARKET_TAX = 0.10   # 买卖各抽 10% 交易税（买家多付、卖家实收），差额回收


def _market(data) -> dict:
    m = data.setdefault("market", {"orders": [], "seq": 0})
    m.setdefault("orders", [])
    m.setdefault("seq", 0)
    return m


NPC_SELLERS = ["鱼贩阿明", "渔夫老陈", "海鲜商人", "夜市老板娘", "码头大叔", "渔村小妹", "早市大妈"]


def _ensure_daily_market(data):
    """每日首次访问鱼市时，生成一批随机 NPC 挂单（鱼类/价格/卖家/数量随机）。"""
    m = _market(data)
    day = _today()
    if data.get("last_market_refresh") == day:
        return
    m["orders"] = [o for o in m["orders"] if not o.get("npc")]
    for _ in range(random.randint(4, 6)):
        fid = random.choice(list(game.FISH.keys()))
        _name, _emoji, _rr, base, (wmin, wmax), _z = game.FISH[fid]
        price = max(1, int(base * random.uniform(0.8, 2.5)))
        qty = random.randint(1, 10)
        m["seq"] += 1
        m["orders"].append({
            "id": m["seq"],
            "seller": "npc",
            "seller_name": random.choice(NPC_SELLERS),
            "npc": True,
            "fid": fid,
            "qty": qty,
            "weight_g": random.randint(wmin, wmax),
            "price": price,
            "total": price * qty,
        })
    data["last_market_refresh"] = day
    _save(data)


def _find_fish_id(name: str):
    for fid, (n, _e, _rr, _b, _w, _z) in game.FISH.items():
        if n == name or name in n:
            return fid
    return None


def _order_line(o, idx) -> str:
    try:
        name, emoji, rr, _b, _w, _z = game.FISH[o["fid"]]
    except KeyError:
        return ""
    seller = o.get("seller_name") or o["seller"]
    return (f"{idx}. {emoji}{name} ×{o['qty']} "
            f"{_RARITY_EMOJI[rr]}{_RARITY_CN[rr]} 单价{o['price']}币 "
            f"总{o['total']}币（卖家{seller}）")


@register(keywords=["鱼市"], help="🏪 玩家交易市场", role=ROLE_ALL, exact=True)
async def cmd_market(ctx):
    with _lock:
        data = _load()
        _ensure_daily_market(data)
        m = _market(data)
        orders = [o for o in m["orders"] if o["qty"] > 0]
    if not orders:
        return await ctx.reply_text(
            "🏪 鱼市空空如也，快去发「挂鱼 <鱼名> <单价>」摆摊喵！\n"
            "· 挂鱼 <鱼名> <单价> — 按条挂售\n"
            "· 买鱼 <单号> — 买下指定单\n"
            "· 撤单 <单号> — 撤回自己的挂单"
        )
    lines = [f"🏪 鱼市（{len(orders)} 单）："]
    for i, o in enumerate(orders, 1):
        line = _order_line(o, i)
        if line:
            lines.append(line)
    lines.append("\n发「买鱼 <单号>」下单，交易抽 10% 税喵")
    return await ctx.reply_text("\n".join(lines))


@register(keywords=["挂鱼"], help="", role=ROLE_ALL, matcher=_starts_with("挂鱼"))
async def cmd_list_fish(ctx):
    parts = _strip_cmd(ctx, "挂鱼").split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return await ctx.reply_text("请发「挂鱼 <鱼名> <单价>」喵，例如：挂鱼 鲤鱼 100")
    price = int(parts[-1])
    if price < 1:
        return await ctx.reply_text("单价至少 1 喵币喵")
    fish_name = "".join(parts[:-1])
    fid = _find_fish_id(fish_name)
    if fid is None:
        return await ctx.reply_text(f"鱼店里没有「{fish_name}」这种鱼喵，发「我的鱼获」看看")
    with _lock:
        data = _load()
        u = _user(data, ctx.openid)
        inv = u["inventory"]
        mine = inv.get(fid, [])
        if not mine:
            return await ctx.reply_text(f"你的背包里没有「{game.FISH[fid][0]}」喵")
        w = mine.pop(0)
        if not mine:
            inv.pop(fid, None)
        m = _market(data)
        m["seq"] += 1
        oid = m["seq"]
        m["orders"].append({
            "id": oid, "seller": str(ctx.openid),
            "seller_name": _sender_name(ctx) or f"用户{str(ctx.openid)[-4:]}",
            "fid": fid, "qty": 1, "weight_g": w, "price": price, "total": price,
        })
        _save(data)
    name = game.FISH[fid][0]
    return await ctx.reply_text(
        f"📦 已把 {name}（{game.fmt_weight(w)}）挂上市场，单价 {price} 喵币（单号 {oid}）\n"
        f"卖出后你实收 {int(price * (1 - MARKET_TAX))} 喵币（扣 10% 交易税）"
    )


@register(keywords=["买鱼"], help="", role=ROLE_ALL, matcher=_starts_with("买鱼"))
async def cmd_buy_fish(ctx):
    n_txt = _strip_cmd(ctx, "买鱼")
    if not n_txt.isdigit():
        return await ctx.reply_text("请发「买鱼 <单号>」喵，例如：买鱼 3")
    order_no = int(n_txt)
    with _lock:
        data = _load()
        _ensure_daily_market(data)
        m = _market(data)
        order = next((o for o in m["orders"] if o["id"] == order_no and o["qty"] > 0), None)
        if order is None:
            return await ctx.reply_text(f"市场里没有单号 {order_no} 的挂单喵，发「鱼市」看看")
        if order["seller"] == str(ctx.openid):
            return await ctx.reply_text("不能买自己挂的鱼喵，先「撤单」吧")
        cost = order["price"]
    if not wallet.spend(ctx.openid, cost):
        return await ctx.reply_text(f"💸 余额不足！买这单需 {cost} 喵币（含 10% 税）")
    with _lock:
        data = _load()
        m = _market(data)
        order = next((o for o in m["orders"] if o["id"] == order_no and o["qty"] > 0), None)
        if order is None or order["seller"] == str(ctx.openid):
            wallet.add(ctx.openid, cost)  # 已被抢单，退回
            return await ctx.reply_text("这单刚被别人买走了喵，手慢了~")
        order["qty"] -= 1
        order["total"] = order["price"] * order["qty"]
        is_npc = bool(order.get("npc"))
        if not is_npc:
            seller_earn = int(order["price"] * (1 - MARKET_TAX))
            wallet.add(order["seller"], seller_earn)
        u = _user(data, ctx.openid)
        u.setdefault("inventory", {}).setdefault(order["fid"], []).append(order["weight_g"])
        _save(data)
    name = game.FISH[order["fid"]][0]
    if is_npc:
        return await ctx.reply_text(
            f"🛒 买下 {name}（{game.fmt_weight(order['weight_g'])}），花费 {cost} 喵币\n"
            f"（NPC 挂单，喵币已回收喵）"
        )
    return await ctx.reply_text(
        f"🛒 买下 {name}（{game.fmt_weight(order['weight_g'])}），花费 {cost} 喵币\n"
        f"卖家实收 {seller_earn} 喵币，10% 交易税已回收喵"
    )


@register(keywords=["撤单", "取消挂单"], help="", role=ROLE_ALL, matcher=_starts_with("撤单", "取消挂单"))
async def cmd_cancel_order(ctx):
    n_txt = _strip_cmd(ctx, "撤单", "取消挂单")
    if not n_txt.isdigit():
        return await ctx.reply_text("请发「撤单 <单号>」喵，例如：撤单 3")
    order_no = int(n_txt)
    with _lock:
        data = _load()
        m = _market(data)
        order = next((o for o in m["orders"] if o["id"] == order_no and o["qty"] > 0), None)
        if order is None:
            return await ctx.reply_text(f"市场里没有单号 {order_no} 的挂单喵")
        if order["seller"] != str(ctx.openid):
            return await ctx.reply_text("只能撤回自己挂的单喵")
        order["qty"] -= 1
        u = _user(data, ctx.openid)
        u.setdefault("inventory", {}).setdefault(order["fid"], []).append(order["weight_g"])
        _save(data)
    name = game.FISH[order["fid"]][0]
    return await ctx.reply_text(f"↩️ 已撤回 {name} 的挂单，鱼退回背包喵")


# ---------- 交易所（大宗商品投资，动态价格，5% 税，保质期腐败） ----------

EXCHANGE_TAX = 0.05
EXCHANGE_GOODS = {
    "dried": ("鱼干", 200, 3),    # key: (名称, 基础价, 保质期天)
    "oil": ("鱼油", 500, 3),
    "roe": ("鱼卵", 1000, 3),
}
EXCHANGE_LIMIT = 500   # 每人持仓上限（份）


def _ex_price(key, day):
    base = EXCHANGE_GOODS[key][1]
    rng = random.Random(day * 1000 + sum(ord(c) for c in key))
    return int(base * (0.8 + rng.random() * 0.4))   # 每日 ±20% 确定性波动


def _today():
    return int(time.strftime("%Y%m%d"))


def _exchange(data) -> dict:
    e = data.setdefault("exchange", {"holdings": {}})
    e.setdefault("holdings", {})
    return e


def _holdings(e, oid) -> dict:
    return e["holdings"].setdefault(str(oid), {})


def _clean_rotten(e, oid, day):
    """清理过期商品，返回被清理的商品名列表。"""
    h = _holdings(e, oid)
    removed = []
    for key, rec in list(h.items()):
        if day - rec["day"] > EXCHANGE_GOODS[key][2]:
            del h[key]
            removed.append(EXCHANGE_GOODS[key][0])
    return removed


def _good_key(name: str):
    for key, (cn, _b, _s) in EXCHANGE_GOODS.items():
        if name in (cn, key):
            return key
    return None


@register(keywords=["交易所"], help="📈 大宗商品投资（低买高卖）", role=ROLE_ALL, matcher=_starts_with("交易所"))
async def cmd_exchange(ctx):
    parts = _strip_cmd(ctx, "交易所").split()
    day = _today()
    if not parts:
        lines = ["📈 交易所行情（每日波动）："]
        for key, (name, _b, shelf) in EXCHANGE_GOODS.items():
            p = _ex_price(key, day)
            lines.append(f"· {name}：{p} 喵币/份（保质期 {shelf} 天）")
        lines.append("\n发「交易所 买 <商品> <数量>」低买高卖，交易抽 5% 税喵")
        return await ctx.reply_text("\n".join(lines))
    action = parts[0]
    if action in ("买", "买入"):
        if len(parts) < 3 or not parts[2].isdigit():
            return await ctx.reply_text("请发「交易所 买 <商品> <数量>」喵，例如：交易所 买 鱼油 10")
        key = _good_key(parts[1])
        if key is None:
            return await ctx.reply_text("商品只有：鱼干 / 鱼油 / 鱼卵 喵")
        qty = int(parts[2])
        if qty < 1:
            return await ctx.reply_text("数量至少 1 份喵")
        price = _ex_price(key, day)
        cost = price * qty
        with _lock:
            data = _load()
            e = _exchange(data)
            h = _holdings(e, ctx.openid)
            _clean_rotten(e, ctx.openid, day)
            cur = sum(rec["qty"] for rec in h.values())
            if cur + qty > EXCHANGE_LIMIT:
                return await ctx.reply_text(
                    f"持仓上限 {EXCHANGE_LIMIT} 份，你已有 {cur} 份喵"
                )
        if not wallet.spend(ctx.openid, cost):
            return await ctx.reply_text(f"💸 余额不足！买 {qty} 份 {EXCHANGE_GOODS[key][0]} 需 {cost} 喵币喵")
        with _lock:
            data = _load()
            e = _exchange(data)
            h = _holdings(e, ctx.openid)
            rec = h.setdefault(key, {"qty": 0, "cost": 0, "day": day})
            rec["qty"] += qty
            rec["cost"] += cost
            rec["day"] = day
            _save(data)
        return await ctx.reply_text(
            f"📥 买入 {EXCHANGE_GOODS[key][0]} ×{qty}，花费 {cost} 喵币（今日价 {price}/份）\n"
            f"发「交易所 卖 {EXCHANGE_GOODS[key][0]} <数量>」择机卖出喵"
        )
    if action in ("卖", "卖出"):
        if len(parts) < 3 or not parts[2].isdigit():
            return await ctx.reply_text("请发「交易所 卖 <商品> <数量>」喵，例如：交易所 卖 鱼油 5")
        key = _good_key(parts[1])
        if key is None:
            return await ctx.reply_text("商品只有：鱼干 / 鱼油 / 鱼卵 喵")
        qty = int(parts[2])
        if qty < 1:
            return await ctx.reply_text("数量至少 1 份喵")
        price = _ex_price(key, day)
        with _lock:
            data = _load()
            e = _exchange(data)
            h = _holdings(e, ctx.openid)
            _clean_rotten(e, ctx.openid, day)
            rec = h.get(key)
            if not rec or rec["qty"] < qty:
                return await ctx.reply_text(f"你没有 {qty} 份 {EXCHANGE_GOODS[key][0]} 喵，发「持仓」看看")
            rec["qty"] -= qty
            if rec["qty"] <= 0:
                del h[key]
            else:
                rec["cost"] = int(rec["cost"] * rec["qty"] / (rec["qty"] + qty))
            gross = price * qty
            tax = int(gross * EXCHANGE_TAX)
            net = gross - tax
            wallet.add(ctx.openid, net)
            _save(data)
        return await ctx.reply_text(
            f"📤 卖出 {EXCHANGE_GOODS[key][0]} ×{qty}，毛收 {gross} 喵币\n"
            f"扣 5% 税 {tax}，实得 {net} 喵币喵"
        )
    return await ctx.reply_text("用法：交易所 / 交易所 买 <商品> <数量> / 交易所 卖 <商品> <数量> 喵")


@register(keywords=["持仓"], help="📦 查看交易所持仓", role=ROLE_ALL, exact=True)
async def cmd_holdings(ctx):
    day = _today()
    with _lock:
        data = _load()
        e = _exchange(data)
        _clean_rotten(e, ctx.openid, day)
        h = _holdings(e, ctx.openid)
        holds = [(key, rec) for key, rec in h.items() if rec["qty"] > 0]
    if not holds:
        return await ctx.reply_text("📦 你还没有大宗商品持仓，发「交易所」看看行情喵")
    lines = ["📦 我的持仓："]
    for key, rec in holds:
        name, _b, shelf = EXCHANGE_GOODS[key]
        cur = _ex_price(key, day)
        cost = rec["cost"]
        pnl = cur * rec["qty"] - cost
        lines.append(
            f"· {name} ×{rec['qty']}（成本 {cost} 喵币，现价 {cur}/份，"
            f"{'📈' if pnl >= 0 else '📉'}盈亏 {pnl:+d}）"
        )
    lines.append("\n发「交易所 卖 <商品> <数量>」卖出喵")
    return await ctx.reply_text("\n".join(lines))


# ---------- 转账（玩家间喵币流转） ----------

@register(keywords=["转账"], help="💸 给 @某人 转账喵币", role=ROLE_ALL, matcher=_starts_with("转账"))
async def cmd_transfer(ctx):
    mention = {}
    for m in getattr(ctx.message, "mentions", None) or []:
        if isinstance(m, dict):
            oid = m.get("member_openid") or m.get("openid") or m.get("id") or ""
            is_you = m.get("is_you")
        else:
            oid = getattr(m, "member_openid", None) or getattr(m, "openid", None) or ""
            is_you = getattr(m, "is_you", None)
        if oid:
            mention[oid] = is_you
    ats = [oid for oid, is_you in mention.items() if not is_you]
    nums = [int(x) for x in re.findall(r"\d+", _strip_cmd(ctx, "转账"))]
    if not ats or not nums:
        return await ctx.reply_text("请发「转账 <@某人> <金额>」喵，例如：转账 @小明 100")
    to_oid = ats[0]
    amount = nums[-1]
    if to_oid == str(ctx.openid):
        return await ctx.reply_text("不能转给自己喵")
    if amount < 1:
        return await ctx.reply_text("金额至少 1 喵币喵")
    if not wallet.transfer(ctx.openid, to_oid, amount):
        return await ctx.reply_text("💸 余额不足喵")
    return await ctx.reply_text(
        f"💸 已转账 {amount} 喵币，当前余额 {wallet.balance(ctx.openid)} 喵喵币"
    )


# ---------- 红包（群内抢红包，社交经济） ----------

REDPACK_MIN = 100          # 红包最少金额
REDPACK_MAX_COUNT = 20     # 最多份数


def _redpacks(data) -> dict:
    return data.setdefault("redpacks", {})


@register(keywords=["发红包"], help="🧧 群内发红包", role=ROLE_ALL, matcher=_starts_with("发红包"))
async def cmd_send_redpack(ctx):
    if ctx.scene != "group":
        return await ctx.reply_text("红包只能在群里发喵")
    parts = _strip_cmd(ctx, "发红包").split()
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        return await ctx.reply_text("请发「发红包 <金额> <份数>」喵，例如：发红包 1000 5")
    total = int(parts[0])
    count = int(parts[1])
    if total < REDPACK_MIN:
        return await ctx.reply_text(f"红包金额至少 {REDPACK_MIN} 喵币喵")
    if count < 1 or count > REDPACK_MAX_COUNT:
        return await ctx.reply_text(f"份数需在 1-{REDPACK_MAX_COUNT} 之间喵")
    if count > total:
        return await ctx.reply_text("份数不能超过金额喵（每份至少 1 喵币）")
    if not wallet.spend(ctx.openid, total):
        return await ctx.reply_text("💸 余额不足喵")
    with _lock:
        data = _load()
        rp = _redpacks(data)
        gid = str(ctx.target)
        packs = rp.setdefault(gid, [])
        packs.append({
            "id": len(packs) + 1,
            "sender": str(ctx.openid),
            "total": total,
            "left": total,
            "count": count,
            "left_count": count,
            "claims": {},
        })
        _save(data)
    return await ctx.reply_text(
        f"🧧 发红包啦！{total} 喵币 × {count} 份\n发「领红包」开抢喵！"
    )


@register(keywords=["领红包", "抢红包"], help="🧧 抢群红包", role=ROLE_ALL, exact=True)
async def cmd_claim_redpack(ctx):
    if ctx.scene != "group":
        return await ctx.reply_text("红包只能在群里领喵")
    oid = str(ctx.openid)
    with _lock:
        data = _load()
        rp = _redpacks(data)
        packs = rp.get(str(ctx.target), [])
        pack = next((p for p in packs if p["left_count"] > 0), None)
        if pack is None:
            return await ctx.reply_text("当前群没有可领的红包喵")
        if oid in pack["claims"]:
            return await ctx.reply_text("你已经领过这个红包啦喵")
        if oid == pack["sender"]:
            return await ctx.reply_text("自己发的红包不能领喵")
        if pack["left_count"] == 1:
            amt = pack["left"]
        else:
            max_a = pack["left"] - (pack["left_count"] - 1)
            amt = random.randint(1, max_a)
        pack["left"] -= amt
        pack["left_count"] -= 1
        pack["claims"][oid] = amt
        wallet.add(oid, amt)
        _save(data)
    return await ctx.reply_text(f"🧧 抢到 {amt} 喵币！手气不错喵")


@register(keywords=["红包列表"], help="🧧 查看群活跃红包", role=ROLE_ALL, exact=True)
async def cmd_redpack_list(ctx):
    if ctx.scene != "group":
        return await ctx.reply_text("红包只能在群里看喵")
    with _lock:
        data = _load()
        packs = _redpacks(data).get(str(ctx.target), [])
        active = [p for p in packs if p["left_count"] > 0]
    if not active:
        return await ctx.reply_text("当前群没有活跃红包喵")
    lines = [f"🧧 当前群活跃红包（{len(active)} 个）："]
    for p in active:
        lines.append(f"· 剩 {p['left']}/{p['total']} 喵币 · {p['left_count']}/{p['count']} 份")
    lines.append("\n发「领红包」开抢喵")
    return await ctx.reply_text("\n".join(lines))
