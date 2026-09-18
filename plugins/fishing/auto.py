# -*- coding: utf-8 -*-
"""🎣 钓鱼插件 · 自动钓鱼（纯时间记录，无后台运行）。
「自动钓鱼」需要一台自动钓鱼机（鱼具店商品，可升级），只记录开始时间戳，
「停止自动钓鱼」时对比时间差按机器属性（间隔/成功率/最大时长）计算收益。"""

import random
import time

from bot.commands import register, ROLE_ALL
from bot.core import wallet
from . import core
from . import game

AUTO_COST = 100            # 启动费用（喵币）
AUTO_INTERVAL = 300        # 兜底：每 5 分钟一竿（旧全局配置兼容，实际按机器等级）
AUTO_MAX_HOURS = 24        # 兜底：最大时长（旧全局配置兼容，实际按机器等级）


def _machine(lv):
    """按等级取机器属性；无机器/未知等级回退到基础机。"""
    return game.AUTO_MACHINES.get(lv, game.AUTO_MACHINES[1])


def _session_level(session, u):
    """会话启动时记录机器等级；旧会话（无 level 字段）回退当前机器或基础机。"""
    return session.get("level") or int(u.get("auto_machine", 0)) or 1


def _effective_seconds(session, now, max_hours):
    """有效时长 = min(已过时间, 机器最大小时数)，超出的部分不计算。"""
    return min(now - session["start"], max_hours * 3600)


def _settle_auto(ctx, data, u, session):
    """停止自动钓鱼：按机器属性对比记录时间计算收益，结算后清除会话。"""
    oid = str(ctx.openid)
    now = int(time.time())
    lv = _session_level(session, u)
    name, _price, interval_min, success, max_hours = _machine(lv)
    interval = interval_min * 60
    elapsed = now - session["start"]
    effective = _effective_seconds(session, now, max_hours)
    n = effective // interval
    if n <= 0:
        u.pop("auto_fish", None)
        core._save(data)
        return (f"🤖 「{name}」运行时间太短，一条都没钓到喵（至少 "
                f"{interval_min} 分钟）")
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
    catches = []
    miss = 0
    for _ in range(n):
        if random.random() > success:
            miss += 1
            continue
        fish = game.roll_fish(rod, [], hook_lv=hook, line_lv=line,
                              fortune_lv=fortune, luck_lv=luck)
        if treasure:
            fish["value"] = int(fish["value"] * (1 + 0.1 * treasure))
        core._record_catch(u, fish, oid)
        catches.append(fish)
    u.pop("auto_fish", None)
    core._save(data)
    total_val = sum(f["value"] for f in catches)
    best = max(catches, key=lambda f: f["value"]) if catches else None
    lines = [
        f"🤖 「{name}」收工：{n} 竿成功 {len(catches)} 条，脱钩 {miss} 竿，"
        f"估值 {total_val} 喵币"
    ]
    if best:
        lines.append(
            f"⭐ 最佳：{best['emoji']} {best['name']} "
            f"({core._RARITY_EMOJI[best['rarity']]}{core._RARITY_CN[best['rarity']]} · 约值 {best['value']} 币)"
        )
    if elapsed > effective:
        lines.append(f"⏳ 机器最长运行 {max_hours} 小时，只计算了 {effective // 3600} 小时喵")
    return "\n".join(lines)


@register(keywords=["自动钓鱼"], help="🤖 自动钓鱼（需自动钓鱼机，鱼具店可买可升级）", role=ROLE_ALL, matcher=core._starts_with("自动钓鱼"))
async def cmd_auto_fish(ctx):
    """自动钓鱼：无机器时展示机器等级表与购买引导；有机器未运行则启动；运行中显示状态。"""
    oid = str(ctx.openid)
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        lv = int(u.get("auto_machine", 0))
        session = u.get("auto_fish")
        if lv <= 0:
            lines = ["🤖 你还没有自动钓鱼机喵，买一台才能自动钓鱼（鱼具店商品，可升级）："]
            for k in sorted(game.AUTO_MACHINES):
                n, p, i, s, m = game.AUTO_MACHINES[k]
                lines.append(
                    f"· 第{k}级 {n} — {p}币（{i}分钟/竿 · 成功率{s * 100:.0f}% · 最长{m}小时）")
            lines.append("买法：发「买钓鱼机」买第一台 · 发「升级钓鱼机」升级")
            return await ctx.reply_text("\n".join(lines))
        if session:
            name, _p, interval_min, _s, max_hours = _machine(lv)
            now = int(time.time())
            elapsed = now - session["start"]
            due = _effective_seconds(session, now, max_hours) // (interval_min * 60)
            return await ctx.reply_text(
                f"🤖 「{name}」运行中喵，已过 {elapsed // 60} 分钟，"
                f"已累计 {due} 竿\n"
                f"发「停止自动钓鱼」结算收鱼喵（机器最长运行 {max_hours} 小时）"
            )
    if not wallet.spend(oid, AUTO_COST):
        return await ctx.reply_text(f"💸 余额不足！启动自动钓鱼需 {AUTO_COST} 喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        if u.get("auto_fish"):
            wallet.add(oid, AUTO_COST)
            return await ctx.reply_text("你已经在自动钓鱼啦，发「停止自动钓鱼」结算喵")
        lv = int(u.get("auto_machine", 0))
        name, _p, interval_min, success, max_hours = _machine(lv)
        u["auto_fish"] = {"start": int(time.time()), "level": lv}
        core._save(data)
    return await ctx.reply_text(
        f"🤖 「{name}」开始自动钓鱼啦！每 {interval_min} 分钟一竿，成功率 {success * 100:.0f}%\n"
        f"最长运行 {max_hours} 小时 · 发「停止自动钓鱼」结算收益~"
    )


@register(keywords=["停止自动钓鱼"], help="🤖 停止自动钓鱼并结算", role=ROLE_ALL, matcher=core._starts_with("停止自动钓鱼"))
async def cmd_stop_auto_fish(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        session = u.get("auto_fish")
        if not session:
            return await ctx.reply_text("你还没有开始自动钓鱼喵，发「自动钓鱼」开始")
        return await ctx.reply_text(_settle_auto(ctx, data, u, session))


@register(keywords=["买钓鱼机"], help="🤖 购买第一台自动钓鱼机", role=ROLE_ALL, exact=True)
async def cmd_buy_machine(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("auto_machine", 0)) > 0:
            return await ctx.reply_text("你已经拥有自动钓鱼机啦，发「升级钓鱼机」升级喵")
    name, price, *_ = game.AUTO_MACHINES[1]
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply_text(f"💸 余额不足，买「{name}」需 {price} 喵喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("auto_machine", 0)) > 0:
            wallet.add(ctx.openid, price)
            return await ctx.reply_text("你已经拥有自动钓鱼机啦，发「升级钓鱼机」升级喵")
        u["auto_machine"] = 1
        core._save(data)
    return await ctx.reply_text(
        f"✅ 购入「{name}」！发「自动钓鱼」开始挂机，发「升级钓鱼机」升级喵")


@register(keywords=["升级钓鱼机"], help="🤖 升级自动钓鱼机", role=ROLE_ALL, exact=True)
async def cmd_upgrade_machine(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        lv = int(u.get("auto_machine", 0))
    if lv <= 0:
        return await ctx.reply_text("你还没有自动钓鱼机喵，发「买钓鱼机」买一台")
    if lv >= max(game.AUTO_MACHINES):
        return await ctx.reply_text("🔝 已经是最高级「秘银钓鱼机」啦喵 ✨")
    nl = lv + 1
    name, price, i, s, m = game.AUTO_MACHINES[nl]
    if not wallet.spend(ctx.openid, price):
        return await ctx.reply_text(f"💸 余额不足，升级到「{name}」需 {price} 喵喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        if int(u.get("auto_machine", 0)) != lv:
            wallet.add(ctx.openid, price)
            return await ctx.reply_text("状态有变化，请重发「升级钓鱼机」喵")
        u["auto_machine"] = nl
        core._save(data)
    return await ctx.reply_text(
        f"✅ 升级到「{name}」！每 {i} 分钟一竿 · 成功率 {s * 100:.0f}% · 最长 {m} 小时喵")
