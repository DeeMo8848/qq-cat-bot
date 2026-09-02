# -*- coding: utf-8 -*-
"""🎣 钓鱼插件 · 自动钓鱼（纯时间记录，无后台运行）。
「自动钓鱼」只记录开始时间戳，「停止自动钓鱼」时对比时间差计算收益；
超过最大小时数的部分不计算，节省性能。"""

import time

from bot.commands import register, ROLE_ALL
from bot.core import wallet
from . import core
from . import game

AUTO_INTERVAL = 300        # 每 5 分钟自动抛一竿
AUTO_COST = 100            # 启动费用（喵币）
AUTO_MAX_HOURS = 24        # 超过该时长（小时）的部分不计算收益


def _effective_seconds(session, now):
    """有效时长 = min(已过时间, 最大小时数)，超出的部分不计算。"""
    return min(now - session["start"], AUTO_MAX_HOURS * 3600)


def _settle_auto(ctx, data, u, session):
    """停止自动钓鱼：对比记录时间计算收益，结算后清除会话。"""
    oid = str(ctx.openid)
    now = int(time.time())
    elapsed = now - session["start"]
    effective = _effective_seconds(session, now)
    n = effective // AUTO_INTERVAL
    if n <= 0:
        u.pop("auto_fish", None)
        core._save(data)
        return (f"🎣 自动钓鱼时间太短，一条都没钓到喵（至少 "
                f"{AUTO_INTERVAL // 60} 分钟）")
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
    for _ in range(n):
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
    lines = [f"🎣 自动钓鱼结束：{n} 竿钓到 {len(catches)} 条，估值 {total_val} 喵币"]
    if best:
        lines.append(
            f"⭐ 最佳：{best['emoji']} {best['name']} "
            f"({core._RARITY_EMOJI[best['rarity']]}{core._RARITY_CN[best['rarity']]} · 约值 {best['value']} 币)"
        )
    if elapsed > effective:
        lines.append(f"⏳ 超过 {AUTO_MAX_HOURS} 小时，只计算了 {effective // 3600} 小时喵")
    return "\n".join(lines)


@register(keywords=["自动钓鱼"], help="🎣 自动钓鱼（记录时间，停止时结算）", role=ROLE_ALL, matcher=core._starts_with("自动钓鱼"))
async def cmd_auto_fish(ctx):
    oid = str(ctx.openid)
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        session = u.get("auto_fish")
        if session:
            now = int(time.time())
            elapsed = now - session["start"]
            due = _effective_seconds(session, now) // AUTO_INTERVAL
            return await ctx.reply_text(
                f"🎣 自动钓鱼进行中喵，已过 {elapsed // 60} 分钟，"
                f"已累计 {due} 竿\n"
                f"发「停止自动钓鱼」结算收鱼喵（超过 {AUTO_MAX_HOURS} 小时的部分不计算）"
            )
    if not wallet.spend(oid, AUTO_COST):
        return await ctx.reply_text(f"💸 余额不足！启动自动钓鱼需 {AUTO_COST} 喵币喵")
    with core._lock:
        data = core._load()
        u = core._user(data, oid)
        if u.get("auto_fish"):
            wallet.add(oid, AUTO_COST)
            return await ctx.reply_text("你已经在自动钓鱼啦，发「停止自动钓鱼」结算喵")
        u["auto_fish"] = {"start": int(time.time())}
        core._save(data)
    return await ctx.reply_text(
        f"🎣 自动钓鱼开始啦！无需后台运行，每 {AUTO_INTERVAL // 60} 分钟算一竿\n"
        f"发「停止自动钓鱼」时按时间差结算，不刷屏喵~"
    )


@register(keywords=["停止自动钓鱼"], help="🎣 停止自动钓鱼并结算", role=ROLE_ALL, matcher=core._starts_with("停止自动钓鱼"))
async def cmd_stop_auto_fish(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
        session = u.get("auto_fish")
        if not session:
            return await ctx.reply_text("你还没有开始自动钓鱼喵，发「自动钓鱼」开始")
        return await ctx.reply_text(_settle_auto(ctx, data, u, session))