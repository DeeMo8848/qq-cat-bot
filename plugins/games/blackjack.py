# -*- coding: utf-8 -*-
"""群聊 21 点小游戏。每个群一间房（按 group_openid 隔离）。

移植自 astrbot 插件 astrbot_plugin_blackjack21（协议 MIT）。
改造点：
  · 玩家身份用 openid（不用 QQ 号/真人昵称，@用 openid 尾号代替）
  · QQ 官方无「群禁言」API，原插件的禁言惩罚改为仅播报点差，不做真实禁言
  · 去掉小丑模式，保留纯 21 点核心玩法
命令：创建21点 / 加入21点 / 开始21点 / 要牌 / 停牌 / 21点状态 / 结束21点 / 21点帮助
"""

import asyncio
import hashlib
import random
import secrets
import time
from dataclasses import dataclass, field

from bot.commands import register, ROLE_ALL

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")

_RULES_TEXT = """📖 21点规则说明

【牌值】
· J、Q、K（花牌）：都算 10 点
· 数字牌 2-10：按牌面数字算点数
· A：可以算 1 点或 11 点，取对你最有利的数值

【目标】
手牌尽量接近 21 点，但不能超过 21 点；超过就爆牌，直接输掉本局。

【流程】
1. /创建21点 — 开房间（发起者=管理员）
2. /加入21点 — 其他人加入
3. /开始21点 — 管理员开局（至少2人）
4. /要牌 或 /停牌 — 同步回合操作
5. /21点状态 — 查看房间
6. /21点帮助 — 查看本规则说明
7. /结束21点 — 管理员结束房间

【结算】
未爆牌且点数最高为本局第一（可并列）；其他人按与第一的点数差播报。"""


@dataclass
class _Player:
    openid: str
    is_host: bool = False
    cards: list[str] = field(default_factory=list)
    stood: bool = False

    @property
    def busted(self) -> bool:
        return hand_value(self.cards) > 21

    @property
    def done(self) -> bool:
        return self.stood or self.busted


@dataclass
class _Room:
    group_id: str
    host_id: str
    phase: str = "waiting"  # waiting | playing | finished
    players: dict[str, _Player] = field(default_factory=dict)
    deck: list[str] = field(default_factory=list)
    round_no: int = 0
    round_actions: dict[str, str] = field(default_factory=dict)  # openid -> hit/stand
    sender: object = None
    started_at_ns: int = 0
    waiting_task: asyncio.Task = None
    round_task: asyncio.Task = None

    @property
    def timeout_s(self) -> int:
        return 60

    @property
    def waiting_s(self) -> int:
        return 600


# group_id -> _Room
_ROOMS: dict[str, _Room] = {}


def _name(openid: str) -> str:
    """用 openid 尾号作为可读标识，避免泄露完整 id。"""
    return f"玩家·{openid[-4:]}"


def _build_deck() -> list[str]:
    deck = [f"{s}{r}" for s in SUITS for r in RANKS]
    salt = hashlib.sha256(
        f"{time.time_ns()}|{secrets.token_hex(16)}".encode()
    ).digest()
    insist = int.from_bytes(salt[:16], "big") ^ secrets.randbits(128)
    random.Random(insist).shuffle(deck)
    random.SystemRandom().shuffle(deck)
    return deck


def _card_point(card: str) -> int:
    rank = card[1:]
    if rank in {"J", "Q", "K"}:
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(cards: list[str]) -> int:
    total = sum(_card_point(c) for c in cards)
    aces = sum(1 for c in cards if c.endswith("A"))
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _fmt(cards: list[str]) -> str:
    return " ".join(cards) if cards else "（暂无）"


def _deal(cards, deck) -> str | None:
    if not deck:
        return None
    card = random.SystemRandom().choice(deck)
    deck.remove(card)
    cards.append(card)
    return card


def _active(room) -> list[_Player]:
    return [p for p in room.players.values() if not p.done]


def _waiting(room) -> list[_Player]:
    return [p for p in _active(room) if p.openid not in room.round_actions]


def _player_status(room, player) -> str:
    if player.busted:
        return "超过21"
    if player.stood:
        return "已停手"
    if player.openid in room.round_actions:
        return "本轮已选·" + ("要牌" if room.round_actions[player.openid] == "hit" else "停手")
    return "本轮未选"


def _room_text(room, *, settle: bool = False) -> str:
    if settle:
        phase = "结算"
    elif room.phase == "waiting":
        phase = "等人中"
    elif room.phase == "playing":
        phase = f"第{room.round_no}轮·限时{room.timeout_s}秒"
    else:
        phase = "已结束"
    lines = [f"🃏 21点（{phase}）"]
    if not settle and room.phase == "playing":
        w = _waiting(room)
        if w:
            lines.append("本轮待操作：" + "、".join(_name(p.openid) for p in w))
    for p in room.players.values():
        if room.phase == "waiting" and not settle:
            lines.append(f"· {_name(p.openid)}（已加入）")
            continue
        if settle:
            status = "结束"
            if p.busted:
                status = "超过21"
            elif p.stood:
                status = "已停手"
        else:
            status = _player_status(room, p)
        lines.append(f"· {_name(p.openid)}：{_fmt(p.cards)}={hand_value(p.cards)}（{status}）")
    return "\n".join(lines)


def _cancel(task):
    if not task:
        return
    try:
        if task is asyncio.current_task():
            return
    except RuntimeError:
        pass
    task.cancel()


def _cancel_timers(room):
    _cancel(room.waiting_task)
    room.waiting_task = None
    _cancel(room.round_task)
    room.round_task = None


async def _bcast(room, text):
    """向整群发一条非引用文本（用于轮次超时/结算等非直接回复场景）。"""
    try:
        await room.sender._send("group", room.group_id, msg_type=0, content=text)
    except Exception:
        pass


def _arm_waiting_timer(room):
    _cancel(room.waiting_task)
    group = room.group_id
    timeout = room.waiting_s

    async def _on_timeout():
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        cur = _ROOMS.get(group)
        if not cur or cur.phase != "waiting":
            return
        _ROOMS.pop(group, None)
        await _bcast(cur, f"⏰ 21点房间创建后 {timeout // 60} 分钟仍未开始，已自动关闭。")

    room.waiting_task = asyncio.create_task(_on_timeout())


def _arm_round_timer(room):
    _cancel(room.round_task)
    group = room.group_id
    round_no = room.round_no
    timeout = room.timeout_s

    async def _on_timeout():
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        cur = _ROOMS.get(group)
        if not cur or cur.phase != "playing" or cur.round_no != round_no:
            return
        waiting = _waiting(cur)
        if not waiting:
            return
        for p in waiting:
            cur.round_actions[p.openid] = "stand"
        head = "⏰ 本轮超时，未操作视为停手：" + "、".join(_name(p.openid) for p in waiting) + "\n"
        await _bcast(cur, head + await _resolve_round(cur))

    room.round_task = asyncio.create_task(_on_timeout())


def _begin_round(room):
    room.round_no += 1
    room.round_actions = {}
    _arm_round_timer(room)


async def _resolve_round(room) -> str:
    """执行本轮所有选择（要牌/停牌），返回本轮播报。"""
    _cancel(room.round_task)
    room.round_task = None
    lines = [f"—— 第{room.round_no}轮结算 ——"]
    for openid, action in list(room.round_actions.items()):
        p = room.players.get(openid)
        if not p or p.done:
            continue
        if action == "stand":
            p.stood = True
            lines.append(f"✋ {_name(openid)} 停手：{_fmt(p.cards)}={hand_value(p.cards)}")
            continue
        card = _deal(p.cards, room.deck)
        if card is None:
            lines.append(f"🃏 {_name(openid)} 要牌失败：牌堆已空")
            continue
        lines.append(f"🃏 {_name(openid)} 要到 {card} → {_fmt(p.cards)}={hand_value(p.cards)}")
        if p.busted:
            lines.append(f"💥 {_name(openid)} 超过21了！")
    return "\n".join(lines)


def _pick_winners(players) -> list[_Player]:
    alive = [p for p in players if not p.busted]
    if alive:
        best = max(hand_value(p.cards) for p in alive)
        return [p for p in alive if hand_value(p.cards) == best]
    best = min(hand_value(p.cards) for p in players)
    return [p for p in players if hand_value(p.cards) == best]


async def _settle(room) -> str:
    _cancel_timers(room)
    players = list(room.players.values())
    winners = _pick_winners(players)
    winner_ids = {p.openid for p in winners}
    winner_score = hand_value(winners[0].cards)
    lines = ["🏁 21点本局结束！", _room_text(room, settle=True), ""]
    lines.append("本局第一：" + "、".join(f"{_name(p.openid)}({hand_value(p.cards)}点)" for p in winners) + " —— 不用禁言")
    for p in players:
        if p.openid in winner_ids:
            continue
        diff = abs(winner_score - hand_value(p.cards))
        lines.append(f"{_name(p.openid)}：{hand_value(p.cards)}点，与第一相差{diff}点")
    room.phase = "finished"
    _ROOMS.pop(room.group_id, None)
    return "\n".join(lines)


async def _after_choice(room, head: str) -> str:
    """处理一次选择之后：等人齐，或结算本轮/结束。"""
    if not _waiting(room):
        parts = [head, await _resolve_round(room)]
        alive = _active(room)
        if not alive:
            parts.append(await _settle(room))
            return "\n".join(parts)
        _begin_round(room)
        parts.append(f"➡️ 第{room.round_no}轮（限时 {room.timeout_s} 秒） /要牌 或 /停牌")
        parts.append(_room_text(room))
        return "\n".join(parts)
    parts = [head, _room_text(room)]
    parts.append("⏳ 还在等：" + "、".join(_name(p.openid) for p in _waiting(room)))
    return "\n".join(parts)


def _need_group(ctx):
    if ctx.scene != "group":
        return True
    return False


@register(keywords=["21点帮助", "21点规则"], help="21点规则说明喵", role=ROLE_ALL)
async def cmd_bj_help(ctx):
    await ctx.reply_text(_RULES_TEXT)


@register(keywords=["创建21点", "开21点"], help="创建21点房间喵", role=ROLE_ALL)
async def cmd_bj_open(ctx):
    if _need_group(ctx):
        await ctx.reply_text("21点只能在群里玩。")
        return
    group = ctx.target
    existing = _ROOMS.get(group)
    if existing and existing.phase != "finished":
        await ctx.reply_text("本群已有进行中的21点，先打完或管理员发 /结束21点。")
        return
    uid = ctx.openid
    room = _Room(group_id=group, host_id=uid, sender=ctx.sender)
    room.players[uid] = _Player(openid=uid, is_host=True)
    _ROOMS[group] = room
    _arm_waiting_timer(room)
    await ctx.reply_text(
        f"🃏 {_name(uid)} 创建了21点房间，并成为管理员！\n"
        "其他人发 /加入21点\n"
        "人齐后管理员发 /开始21点\n"
        "规则说明：/21点帮助\n"
        f"（{room.waiting_s // 60} 分钟内未开始将自动关闭）\n\n"
        + _room_text(room)
    )


@register(keywords=["加入21点"], help="加入21点房间喵", role=ROLE_ALL)
async def cmd_bj_join(ctx):
    if _need_group(ctx):
        await ctx.reply_text("21点只能在群里玩。")
        return
    group = ctx.target
    room = _ROOMS.get(group)
    if not room or room.phase != "waiting":
        await ctx.reply_text("当前没有可加入的21点房间，先 /创建21点。")
        return
    uid = ctx.openid
    if uid in room.players:
        await ctx.reply_text("你已经在房间里了。")
        return
    if len(room.players) >= 6:
        await ctx.reply_text("满员了（最多 6 人）。")
        return
    room.players[uid] = _Player(openid=uid)
    room.sender = ctx.sender
    await ctx.reply_text(f"✅ {_name(uid)} 已加入！\n\n" + _room_text(room))


@register(keywords=["开始21点"], help="管理员开局21点喵", role=ROLE_ALL)
async def cmd_bj_deal(ctx):
    if _need_group(ctx):
        await ctx.reply_text("21点只能在群里玩。")
        return
    group = ctx.target
    room = _ROOMS.get(group)
    if not room or room.phase != "waiting":
        await ctx.reply_text("没有等待中的21点房间，先 /创建21点。")
        return
    if ctx.openid != room.host_id:
        await ctx.reply_text("只有本局管理员能 /开始21点。")
        return
    if len(room.players) < 2:
        await ctx.reply_text("至少还要再来 1 人 /加入21点 才能开始。")
        return
    room.sender = ctx.sender
    room.started_at_ns = time.time_ns()
    room.deck = _build_deck()
    room.phase = "playing"
    _cancel(room.waiting_task)
    room.waiting_task = None
    for _ in range(2):
        for p in room.players.values():
            _deal(p.cards, room.deck)
    for p in room.players.values():
        if hand_value(p.cards) == 21:
            p.stood = True
    if not _active(room):
        await ctx.reply_text("🃏 开局后无人可继续操作。\n\n" + await _settle(room))
        return
    _begin_round(room)
    await ctx.reply_text(
        "🃏 开局！同步回合制：本轮所有未停手的人都要选一次，全员选完后才统一要牌并进入下一轮。\n"
        f"每轮限时 {room.timeout_s} 秒，超时未操作视为停手。\n"
        "已停手的人不能再要牌。\n"
        "规则：/21点帮助\n"
        "请发 /要牌 或 /停牌\n\n"
        + _room_text(room)
    )


@register(keywords=["要牌"], help="21点要牌喵", role=ROLE_ALL)
async def cmd_bj_hit(ctx):
    room = _ROOMS.get(ctx.target or "")
    if not room or room.phase != "playing":
        await ctx.reply_text("现在没有进行中的21点。")
        return
    room.sender = ctx.sender
    uid = ctx.openid
    p = room.players.get(uid)
    if not p:
        await ctx.reply_text("你不在这个房间。")
        return
    if p.stood:
        await ctx.reply_text("你已经停手了，不能再要牌。")
        return
    if p.busted:
        await ctx.reply_text("你已经超过21，不能再要牌。")
        return
    if uid in room.round_actions:
        await ctx.reply_text("你本轮已经选过了，等其他人选完。")
        return
    room.round_actions[uid] = "hit"
    await ctx.reply_text(await _after_choice(room, f"📝 {_name(uid)} 本轮选择：要牌（等全员选完再发）"))


@register(keywords=["停牌", "停手"], help="21点停牌喵", role=ROLE_ALL)
async def cmd_bj_stand(ctx):
    room = _ROOMS.get(ctx.target or "")
    if not room or room.phase != "playing":
        await ctx.reply_text("现在没有进行中的21点。")
        return
    room.sender = ctx.sender
    uid = ctx.openid
    p = room.players.get(uid)
    if not p:
        await ctx.reply_text("你不在这个房间。")
        return
    if p.done:
        await ctx.reply_text("你已经停手或超过21了。")
        return
    if uid in room.round_actions:
        await ctx.reply_text("你本轮已经选过了，等其他人选完。")
        return
    room.round_actions[uid] = "stand"
    await ctx.reply_text(await _after_choice(room, f"📝 {_name(uid)} 本轮选择：停手（确认后本局不能再要）"))


@register(keywords=["21点状态"], help="查看21点房间状态喵", role=ROLE_ALL)
async def cmd_bj_status(ctx):
    room = _ROOMS.get(ctx.target or "")
    if not room:
        await ctx.reply_text("本群没有21点房间。发 /创建21点 开一局。")
        return
    await ctx.reply_text(_room_text(room))


@register(keywords=["结束21点"], help="结束21点房间喵", role=ROLE_ALL)
async def cmd_bj_end(ctx):
    room = _ROOMS.get(ctx.target or "")
    if not room:
        await ctx.reply_text("本群没有21点房间。")
        return
    if ctx.openid != room.host_id:
        await ctx.reply_text("只有本局管理员能结束房间。")
        return
    _cancel_timers(room)
    _ROOMS.pop(room.group_id, None)
    await ctx.reply_text("21点房间已结束。")