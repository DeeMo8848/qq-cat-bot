# -*- coding: utf-8 -*-
"""🏅 钓鱼插件 · 自动称号系统。
根据钓鱼次数 / 财富 / 图鉴进度自动计算称号，无需手动领取。"""

from bot.commands import register, ROLE_ALL
from bot.core import wallet
from . import core

TITLE_TRACKS = [
    ("🎣 钓鱼", [
        (0, "新手渔夫"), (10, "见习渔夫"), (50, "熟练渔夫"),
        (200, "资深渔夫"), (500, "传奇渔夫"),
    ], lambda u, oid: core._stat(u)["catches"]),
    ("💰 财富", [
        (0, "穷光蛋"), (1000, "小有积蓄"), (10000, "小富翁"),
        (100000, "大富翁"), (1000000, "喵币大亨"),
    ], lambda u, oid: wallet.balance(oid)),
    ("📖 图鉴", [
        (0, "图鉴新手"), (5, "图鉴收藏家"), (15, "图鉴大师"),
        (core.TOTAL_SPECIES, "全图鉴收藏家"),
    ], lambda u, oid: len(u.get("codex", {}))),
]


def _tier(track, val):
    """返回 (层级序号, 当前称号, 下一级或 None)。"""
    tiers = track[1]
    idx = 0
    cur = tiers[0]
    nxt = None
    for i, (threshold, name) in enumerate(tiers):
        if val >= threshold:
            idx = i
            cur = (threshold, name)
        else:
            nxt = (threshold, name)
            break
    return idx, cur, nxt


def get_title(u, oid):
    """返回当前最高称号名（各维度层级序号最大者）。"""
    best_name = "新手渔夫"
    best_idx = -1
    for track in TITLE_TRACKS:
        idx, cur, _nxt = _tier(track, track[2](u, oid))
        if idx > best_idx:
            best_idx = idx
            best_name = cur[1]
    return best_name


@register(keywords=["钓鱼称号"], help="🏅 查看钓鱼称号", role=ROLE_ALL, exact=True)
async def cmd_title(ctx):
    with core._lock:
        data = core._load()
        u = core._user(data, ctx.openid)
    lines = ["🏅 钓鱼称号："]
    best_name = "新手渔夫"
    best_idx = -1
    for track in TITLE_TRACKS:
        val = track[2](u, ctx.openid)
        idx, cur, nxt = _tier(track, val)
        if idx > best_idx:
            best_idx = idx
            best_name = cur[1]
        if nxt:
            lines.append(f"{track[0]}：{cur[1]}（还差 {nxt[0] - val} 到「{nxt[1]}」）")
        else:
            lines.append(f"{track[0]}：{cur[1]}（已满级 ✨）")
    lines.append(f"\n当前称号：**{best_name}**")
    return await ctx.reply_text("\n".join(lines))