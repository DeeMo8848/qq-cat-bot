# -*- coding: utf-8 -*-
"""🐟 钓鱼插件 · 社交互动（偷鱼 / 电鱼 / 鱼缸）。
纯虚拟喵喵币娱乐，不涉及真实资金。资金统一走 bot.core.wallet。"""

import random
import time

from bot.commands import register, ROLE_ALL, ROLE_ADMIN
from bot.core import wallet
from config import STATIC_PUBLIC_URL
from . import core
from . import game

STEAL_COOLDOWN = 120       # 偷鱼冷却（秒）
STEAL_RATE = 0.35          # 偷鱼基础成功率
ELECTRIC_COST = 300        # 电鱼电费
ELECTRIC_RATE = 0.65       # 电鱼成功率
ELECTRIC_FINE = 200        # 电鱼失败天罚罚款
ELECTRIC_COOLDOWN = 300    # 电鱼冷却（秒，5 分钟）
AQUARIUM_LIMIT = 60        # 基础鱼缸容量（买「鱼缸」后获得；升级走 game.AQUARIUM_TANKS，Web 后台可调）


def _target_oid(ctx):
    """提取 @ 的第一个非本人 openid。"""
    for m in getattr(ctx.message, "mentions", None) or []:
        if isinstance(m, dict):
            oid = m.get("member_openid") or m.get("openid") or m.get("id") or ""
            is_you = m.get("is_you")
        else:
            oid = getattr(m, "member_openid", None) or getattr(m, "openid", None) or ""
            is_you = getattr(m, "is_you", None)
        if oid and not is_you:
            return str(oid)
    return None


def _pick_fish(inv):
    """从背包随机挑一条鱼，返回 (fid, weight_g)；空背包返回 None。"""
    fids = [fid for fid, ws in inv.items() if ws]
    if not fids:
        return None
    fid = random.choice(fids)
    ws = inv[fid]
    w = ws.pop(0)
    if not ws:
        inv.pop(fid, None)
    return fid, w


# ---------- 偷鱼（社交互动，有失败率） ----------

@register(keywords=["偷鱼"], help="🐱 偷 @某人 一条鱼（有失败率）", role=ROLE_ALL, matcher=core._starts_with("偷鱼"))
async def cmd_steal(ctx):
    oid = str(ctx.openid)
    target = _target_oid(ctx)
    if target is None:
        return await ctx.reply_text("请发「偷鱼 @某人」喵，例如：偷鱼 @小明")
    if target == oid:
        return await ctx.reply_text("不能偷自己喵！")
    now = int(time.time())
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        last = u.get("last_steal", 0)
        if now - last < STEAL_COOLDOWN:
            wait = STEAL_COOLDOWN - (now - last)
            return await ctx.reply_text(f"🐱 手痒也要等等喵，{wait} 秒后再偷")
        tu = core._user(data, target)
        stealable = {fid: ws for fid, ws in tu.get("inventory", {}).items() if ws}
        if not stealable:
            return await ctx.reply_text("🐱 对方背包里没有鱼可偷喵（鱼缸里的偷不到）")
        if random.random() > STEAL_RATE:
            u["last_steal"] = now
            core._save(data)
            return await ctx.reply_text("🐱 偷偷摸摸…被发现了！什么都没偷到喵")
        fid, w = _pick_fish(stealable)
        tu["inventory"] = stealable
        u.setdefault("inventory", {}).setdefault(fid, []).append(w)
        u["last_steal"] = now
        core._save(data)
    name = game.FISH[fid][0]
    return await ctx.reply_text(
        f"🐱 得手啦！偷到 {name}（{game.fmt_weight(w)}）一条喵！"
    )


# ---------- 电鱼（高风险高回报，失败遭天罚） ----------

@register(keywords=["电鱼"], help="⚡ 电鱼（高风险，成功一网打尽，失败遭天罚）", role=ROLE_ALL, matcher=core._starts_with("电鱼"))
async def cmd_electric(ctx):
    oid = str(ctx.openid)
    now = int(time.time())
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        last = u.get("last_electric", 0)
        if now - last < ELECTRIC_COOLDOWN:
            wait = ELECTRIC_COOLDOWN - (now - last)
            return await ctx.reply_text(f"⚡ 电鱼机还在充电喵，{wait} 秒后再来")
    if not wallet.spend(oid, ELECTRIC_COST):
        return await ctx.reply_text(f"💸 余额不足！电鱼需交 {ELECTRIC_COST} 喵币电费喵")
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        rod = int(u.get("rod", 1))
        hook = int(u.get("hook", 1))
        line = int(u.get("line", 1))
        flt = int(u.get("float", 1))
        ench = u.get("ench", {})
        rod_ench = ench.get("rod", {})
        float_ench = ench.get("float", {})
        fortune = rod_ench.get("fortune", 0)
        luck = rod_ench.get("luck", 0)
        treasure = float_ench.get("treasure", 0)
        if random.random() > ELECTRIC_RATE:
            wallet.spend(oid, ELECTRIC_FINE)
            u["last_electric"] = now
            core._save(data)
            return await ctx.reply_text(
                f"⚡ 电鱼被渔政逮个正着！罚款 {ELECTRIC_FINE} 喵币，"
                f"电费 {ELECTRIC_COST} 也打了水漂喵…"
            )
        n = random.randint(3, 5)
        catches = []
        for _ in range(n):
            fish = game.roll_fish(rod, [], hook_lv=hook, line_lv=line,
                                  fortune_lv=fortune, luck_lv=luck)
            if treasure:
                fish["value"] = int(fish["value"] * (1 + 0.1 * treasure))
            core._record_catch(u, fish, oid)
            catches.append(fish)
        new_ach = core._check_achievements(u, oid)
        u["last_electric"] = now
        core._save(data)
    lines = [f"⚡ 电鱼成功！一网捞起 {len(catches)} 条喵！"]
    for fish in catches:
        lines.append(
            f"· {fish['emoji']} {fish['name']} "
            f"({core._RARITY_EMOJI[fish['rarity']]}{core._RARITY_CN[fish['rarity']]} · 约值 {fish['value']} 币)"
        )
    lines += [f"🏅 达成成就「{n}」+{r}喵币！" for n, r in new_ach]
    return await ctx.reply_text("\n".join(lines))


# ---------- 鱼缸（观赏，可防偷） ----------

@register(keywords=["鱼缸"], help="🐠 查看我的鱼缸（观赏，可防偷）", role=ROLE_ALL, exact=True)
async def cmd_aquarium(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("aquarium_limit", 0)) <= 0:
            return await ctx.reply_text("你还没有鱼缸喵，鱼具店发「买鱼缸」买一个（容量 60 条，可升级）")
    from plugins.cards import carddata as cd
    nick = core._sender_name(ctx)
    rec = cd.album(ctx.openid, nick or "我的鱼缸")
    head = "🐠 " + (nick + "的鱼缸：" if nick else "我的鱼缸：")
    return await ctx.reply(head + "\n" + STATIC_PUBLIC_URL + "/aquarium/" + rec["key"])


@register(keywords=["测试鱼缸"], help="🐠 展示测试用鱼缸（仅管理员）", role=ROLE_ADMIN, exact=True)
async def cmd_test_aquarium(ctx):
    return await ctx.reply(f"🐠 测试鱼缸\n{STATIC_PUBLIC_URL}/aquarium/test")


@register(keywords=["存鱼"], help="", role=ROLE_ALL, matcher=core._starts_with("存鱼"))
async def cmd_store_fish(ctx):
    parts = core._strip_cmd(ctx, "存鱼").split()
    qty = 1
    if parts and parts[0].isdigit():
        qty = int(parts[0])
        name_parts = parts[1:]
    else:
        name_parts = parts
    if not name_parts:
        return await ctx.reply_text("请发「存鱼 <数量> <鱼名>」或「存鱼 <鱼名>」喵，例如：存鱼 3 鲤鱼")
    fish_name = "".join(name_parts)
    fid = core._find_fish_id(fish_name)
    if fid is None:
        return await ctx.reply_text(f"鱼店里没有「{fish_name}」这种鱼喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        inv = u.get("inventory", {})
        mine = inv.get(fid, [])
        if not mine:
            return await ctx.reply_text(f"你的背包里没有「{game.FISH[fid][0]}」喵")
        if qty > len(mine):
            qty = len(mine)
        aqua = u.setdefault("aquarium", {})
        limit = int(u.get("aquarium_limit", 0))
        if limit <= 0:
            return await ctx.reply_text(
                "你还没有鱼缸喵，鱼具店发「买鱼缸」买一个（容量 60 条，可升级）")
        cur = sum(len(ws) for ws in aqua.values())
        if cur + qty > limit:
            return await ctx.reply_text(
                f"鱼缸容量上限 {limit} 条，已放 {cur} 条喵，发「升级鱼缸」扩容")
        weights = mine[:qty]
        del mine[:qty]
        if not mine:
            inv.pop(fid, None)
        aqua.setdefault(fid, []).extend(weights)
        core._save(data)
    name = game.FISH[fid][0]
    return await ctx.reply_text(f"🐠 已把 {name} ×{qty} 存进鱼缸，偷鱼贼偷不到啦喵！")


@register(keywords=["取鱼"], help="", role=ROLE_ALL, matcher=core._starts_with("取鱼"))
async def cmd_take_fish(ctx):
    parts = core._strip_cmd(ctx, "取鱼").split()
    qty = 1
    if parts and parts[0].isdigit():
        qty = int(parts[0])
        name_parts = parts[1:]
    else:
        name_parts = parts
    if not name_parts:
        return await ctx.reply_text("请发「取鱼 <数量> <鱼名>」或「取鱼 <鱼名>」喵，例如：取鱼 2 鲤鱼")
    fish_name = "".join(name_parts)
    fid = core._find_fish_id(fish_name)
    if fid is None:
        return await ctx.reply_text(f"鱼店里没有「{fish_name}」这种鱼喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        aqua = u.setdefault("aquarium", {})
        mine = aqua.get(fid, [])
        if not mine:
            return await ctx.reply_text(f"鱼缸里没有「{game.FISH[fid][0]}」喵")
        if qty > len(mine):
            qty = len(mine)
        weights = mine[:qty]
        del mine[:qty]
        if not mine:
            aqua.pop(fid, None)
        u.setdefault("inventory", {}).setdefault(fid, []).extend(weights)
        core._save(data)
    name = game.FISH[fid][0]
    return await ctx.reply_text(f"🐠 已从鱼缸取出 {name} ×{qty} 喵")


@register(keywords=["买鱼缸"], help="🐠 购买鱼缸（观赏防偷）", role=ROLE_ALL, exact=True)
async def cmd_buy_aquarium(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("aquarium_limit", 0)) > 0:
            return await ctx.reply_text("你已经拥有鱼缸啦，发「升级鱼缸」扩大容量喵")
    name, price, cap = game.AQUARIUM_TANKS[1]
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply_text(f"💸 余额不足，买「{name}」需 {price} 喵喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("aquarium_limit", 0)) > 0:
            wallet.add(ctx.openid, price)
            return await ctx.reply_text("你已经拥有鱼缸啦，发「升级鱼缸」扩大容量喵")
        u["aquarium_limit"] = AQUARIUM_LIMIT
        core._save(data)
    return await ctx.reply_text(
        f"✅ 购入「{name}」！容量 {AQUARIUM_LIMIT} 条，发「鱼缸」欣赏，"
        f"发「升级鱼缸」扩容（最高 200 条）喵")


@register(keywords=["升级鱼缸"], help="🐠 升级鱼缸扩大容量", role=ROLE_ALL, exact=True)
async def cmd_upgrade_aquarium(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        cur = int(u.get("aquarium_limit", 0))
    if cur <= 0:
        return await ctx.reply_text("你还没有鱼缸喵，发「买鱼缸」买一个")
    max_cap = game.AQUARIUM_TANKS[max(game.AQUARIUM_TANKS)][2]
    if cur >= max_cap:
        return await ctx.reply_text("🔝 已经是最大容量「豪华鱼缸」啦喵 ✨")
    nxt = None
    for lv in sorted(game.AQUARIUM_TANKS):
        if game.AQUARIUM_TANKS[lv][2] > cur:
            nxt = lv
            break
    name, price, cap = game.AQUARIUM_TANKS[nxt]
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply_text(f"💸 余额不足，升级到「{name}」需 {price} 喵喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("aquarium_limit", 0)) != cur:
            wallet.add(ctx.openid, price)
            return await ctx.reply_text("状态有变化，请重发「升级鱼缸」喵")
        u["aquarium_limit"] = cap
        core._save(data)
    return await ctx.reply_text(f"✅ 升级到「{name}」！容量 {cap} 条喵")