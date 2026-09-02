# -*- coding: utf-8 -*-
"""🎲 钓鱼插件 · 赌博小游戏（骰宝 / 命运之轮 / 擦弹）。
纯虚拟喵喵币娱乐，不涉及真实资金。资金统一走 bot.core.wallet。"""

import random

from bot.commands import register, ROLE_ALL
from bot.core import wallet
from . import core


# ---------- 骰宝（即时结算） ----------
# 类型：大(11-17)/小(4-10)/单/双/豹子/点数(4-17)
# 赔率：大小单双 1:1 · 豹子 1:24 · 点数出现1次1:1 / 2次1:2 / 3次1:12
# 全1(3点)或全6(18点)视为豹子，大小单双通吃

_SICBO_TYPES = ("大", "小", "单", "双", "豹子")


@register(keywords=["骰宝"], help="🎲 骰宝下注（大/小/单/双/豹子/点数）", role=ROLE_ALL, matcher=core._starts_with("骰宝"))
async def cmd_sicbo(ctx):
    parts = core._strip_cmd(ctx, "骰宝").split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return await ctx.reply_text(
            "请发「骰宝 <类型> <金额>」喵，例如：骰宝 大 100\n"
            "类型：大 / 小 / 单 / 双 / 豹子 / 点数(4-17)\n"
            "赔率：大小单双 1:1 · 豹子 1:24 · 点数 1~3 次 1:1~1:12"
        )
    bet = parts[0]
    amount = int(parts[-1])
    if amount < 1:
        return await ctx.reply_text("金额至少 1 喵币喵")
    if not wallet.spend(ctx.openid, amount):
        return await ctx.reply_text("💸 余额不足喵")
    d = [random.randint(1, 6) for _ in range(3)]
    total = sum(d)
    is_same = len(set(d)) == 1
    win = False
    mult = 0
    if bet in _SICBO_TYPES:
        if is_same:
            win = (bet == "豹子")
            mult = 25
        elif bet == "大":
            win = 11 <= total <= 17
            mult = 2
        elif bet == "小":
            win = 4 <= total <= 10
            mult = 2
        elif bet == "单":
            win = total % 2 == 1
            mult = 2
        elif bet == "双":
            win = total % 2 == 0
            mult = 2
    elif bet.isdigit() and 4 <= int(bet) <= 17:
        cnt = d.count(int(bet))
        if cnt >= 1:
            win = True
            mult = (1, 2, 12)[cnt - 1]
    else:
        wallet.add(ctx.openid, amount)
        return await ctx.reply_text("类型不对喵：大 / 小 / 单 / 双 / 豹子 / 点数(4-17)")
    if win:
        prize = amount * mult
        wallet.add(ctx.openid, prize)
        return await ctx.reply_text(
            f"🎲 骰子：{d[0]} {d[1]} {d[2]}（和 {total}）\n"
            f"✅ 押「{bet}」赢了！+{prize} 喵币喵"
        )
    return await ctx.reply_text(
        f"🎲 骰子：{d[0]} {d[1]} {d[2]}（和 {total}）\n"
        f"❌ 押「{bet}」输了，-{amount} 喵币喵"
    )


# ---------- 命运之轮（10 层挑战，成功率递减） ----------
# 第 n 层奖金 = 投入 × 1.55 × 2^(n-1)，第1层期望微盈利吸引，之后庄家优势递增

_WHEEL_SESSIONS = {}
_WHEEL_MAX = 10


def _wheel_rate(level):
    return max(0.20, 0.65 - 0.05 * (level - 1))


@register(keywords=["命运之轮"], help="🎡 高风险轮盘（发「继续挑战/放弃挑战」）", role=ROLE_ALL, matcher=core._starts_with("命运之轮"))
async def cmd_wheel(ctx):
    parts = core._strip_cmd(ctx, "命运之轮").split()
    if len(parts) != 1 or not parts[0].isdigit():
        return await ctx.reply_text(
            "请发「命运之轮 <金额>」喵，例如：命运之轮 100\n"
            "共 10 层，成功率逐层递减，通关倍率高达数百倍！"
        )
    amount = int(parts[0])
    if amount < 1:
        return await ctx.reply_text("金额至少 1 喵币喵")
    if not wallet.spend(ctx.openid, amount):
        return await ctx.reply_text("💸 余额不足喵")
    _WHEEL_SESSIONS[ctx.openid] = {"amount": amount, "level": 1, "pot": amount}
    return await _wheel_turn(ctx)


@register(keywords=["继续挑战"], help="", role=ROLE_ALL, exact=True)
async def cmd_wheel_continue(ctx):
    if ctx.openid not in _WHEEL_SESSIONS:
        return await ctx.reply_text("你还没开始命运之轮喵，发「命运之轮 <金额>」")
    return await _wheel_turn(ctx)


@register(keywords=["放弃挑战"], help="", role=ROLE_ALL, exact=True)
async def cmd_wheel_giveup(ctx):
    s = _WHEEL_SESSIONS.pop(ctx.openid, None)
    if not s:
        return await ctx.reply_text("你还没开始命运之轮喵")
    wallet.add(ctx.openid, s["pot"])
    return await ctx.reply_text(f"🎡 你放弃了挑战，带走 {s['pot']} 喵币喵")


async def _wheel_turn(ctx):
    s = _WHEEL_SESSIONS.get(ctx.openid)
    level = s["level"]
    rate = _wheel_rate(level)
    if random.random() < rate:
        s["pot"] = int(s["amount"] * 1.55 * (2 ** (level - 1)))
        s["level"] += 1
        if s["level"] > _WHEEL_MAX:
            pot = s["pot"]
            amount = s["amount"]
            _WHEEL_SESSIONS.pop(ctx.openid, None)
            return await ctx.reply_text(
                f"🎡 通关全部 {_WHEEL_MAX} 层！获得 {pot} 喵币"
                f"（{pot // amount} 倍）喵！"
            )
        return await ctx.reply_text(
            f"🎡 第 {level} 层成功！当前奖金 {s['pot']} 喵币\n"
            f"发「继续挑战」挑战第 {s['level']} 层（成功率 {int(_wheel_rate(s['level']) * 100)}%），"
            f"或「放弃挑战」拿走奖金喵"
        )
    _WHEEL_SESSIONS.pop(ctx.openid, None)
    return await ctx.reply_text(
        f"🎡 第 {level} 层失败！奖金清零，投入的 {s['amount']} 喵币没了喵"
    )


# ---------- 擦弹（随机倍率） ----------

_ERASER_TABLE = [
    (0.0, 0.2, 10000), (0.2, 0.5, 18000), (0.5, 0.8, 15000),
    (0.8, 1.2, 25000), (1.2, 2.0, 14100), (2.0, 3.0, 4230),
    (3.0, 6.0, 705), (6.0, 15.0, 106), (15.0, 50.0, 21),
    (50.0, 200.0, 7),
]


def _roll_eraser():
    total_w = sum(w for _lo, _hi, w in _ERASER_TABLE)
    r = random.uniform(0, total_w)
    acc = 0
    for lo, hi, w in _ERASER_TABLE:
        acc += w
        if r <= acc:
            return random.uniform(lo, hi)
    return 1.0


@register(keywords=["擦弹"], help="💣 擦弹（随机倍率）", role=ROLE_ALL, matcher=core._starts_with("擦弹"))
async def cmd_eraser(ctx):
    parts = core._strip_cmd(ctx, "擦弹").split()
    if len(parts) != 1:
        return await ctx.reply_text("请发「擦弹 <金额>」或「擦弹 allin/halfin」喵，例如：擦弹 100")
    arg = parts[0]
    if arg == "allin":
        amount = wallet.balance(ctx.openid)
    elif arg == "halfin":
        amount = wallet.balance(ctx.openid) // 2
    elif arg.isdigit():
        amount = int(arg)
    else:
        return await ctx.reply_text("请发「擦弹 <金额>」或「擦弹 allin/halfin」喵")
    if amount < 1:
        return await ctx.reply_text("金额至少 1 喵币喵")
    if not wallet.spend(ctx.openid, amount):
        return await ctx.reply_text("💸 余额不足喵")
    mult = _roll_eraser()
    prize = int(amount * mult)
    wallet.add(ctx.openid, prize)
    diff = prize - amount
    emoji = "💰" if diff >= 0 else "💸"
    return await ctx.reply_text(
        f"💣 擦弹！倍率 {mult:.2f}x\n"
        f"{emoji} {'+' if diff >= 0 else ''}{diff} 喵币（当前余额 {wallet.balance(ctx.openid)}）喵"
    )
