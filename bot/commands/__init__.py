# -*- coding: utf-8 -*-
"""命令系统框架。

设计目标：以后加一个新功能「不用大改项目」。
做法：在 bot/commands/ 里新建一个文件，写一个函数并用 @register 注册即可。
注册后机器人会自动把它加进「功能菜单」并开始响应它的触发词。

命令函数约定：
    async def cmd_xxx(ctx):
        pass
其中 ctx 上有：
    ctx.client  : 机器人客户端（含 self.api）
    ctx.message : 收到的那条消息对象
    ctx.sender  : Sender 实例，用来发消息
    ctx.scene   : "group" / "c2c" / "guild"
    ctx.target  : 目标 id
    ctx.args    : 触发词后面跟的参数（字符串，可为空）
    ctx.reply(text) : 便捷方法，引用式回复文本
"""

from config import MENU_KEYWORDS, BOT_ADMINS, BOT_ASSISTANTS

from bot.core import state

import time

# 已注册的命令函数列表（每个函数通过 @register 加入）
_COMMANDS = []

# 最近已处理的消息 id（防止 WebSocket 与 Webhook 双通道对同一条消息重复响应）
_RECENT_IDS = {}
_DEDUP_WINDOW = 10

# 命令权限级别
ROLE_ALL = "all"          # 所有人可触发
ROLE_ASSISTANT = "assistant"  # 管理员 + 协助者
ROLE_ADMIN = "admin"      # 仅管理员


def _extract_openid(message):
    """从消息对象中提取发送者的 openid（群聊 member_openid / 单聊 user_openid）。"""
    author = getattr(message, "author", None)
    if author is None:
        return ""
    if isinstance(author, dict):
        return author.get("member_openid") or author.get("user_openid") or author.get("id") or ""
    return (
        getattr(author, "member_openid", None)
        or getattr(author, "user_openid", None)
        or ""
    )


def _role_ok(func, openid):
    """命令的权限级别是否允许该 openid 触发。"""
    role = getattr(func, "role", ROLE_ALL)
    if role == ROLE_ALL:
        return True
    if openid in BOT_ADMINS:
        return True
    if role == ROLE_ASSISTANT and openid in BOT_ASSISTANTS:
        return True
    return False


def _group_allowed(func, ctx):
    """命令是否允许在当前群的群黑白名单内生效（非群场景一律放行）。"""
    if ctx.scene != "group":
        return True
    goid = getattr(ctx, "target", None) or getattr(ctx.message, "group_openid", None)
    if not goid:
        return True
    return state.allowed_in_group(func.__name__, goid)


class CommandCtx:
    """一次命令执行时的上下文，把常用信息都挂在上面，命令函数直接用。"""

    def __init__(self, client, message, sender):
        self.client = client
        self.message = message
        self.sender = sender
        self.scene, self.target = sender.scene_of(message)
        self.openid = _extract_openid(message)
        self.keyword = None
        self.args = ""

    async def reply(self, text):
        """引用式回复文本到当前会话。"""
        return await self.sender.send_text(self.message, text, reply=True)

    async def reply_text(self, text, reply=False):
        """普通发送文本（reply=False 时不引用，不占用消息回执）。"""
        return await self.sender.send_text(self.message, text, reply=reply)


def register(keywords, help="", matcher=None, role=ROLE_ALL, exact=False):
    """命令装饰器。keywords 是触发词列表，help 是该命令在菜单里的说明。

    matcher: 可选的自定义匹配函数 matcher(text)->bool，在关键词匹配之前先检查。
            适合「消息里含 BV 号就触发」这类无法用固定关键词表达的场景。
    role: 权限级别，ROLE_ALL（默认）/ ROLE_ASSISTANT / ROLE_ADMIN。
    exact: True 时只允许整条消息精确命中触发词（同「菜单」），
           不会被子串误触发（如「· 抽猪」「我要抽猪」不触发「抽猪」）。
    """
    def decorator(func):
        func.keywords = keywords
        func.help = help
        func.role = role
        func.exact = exact
        m = matcher
        if m is None and exact:
            # exact 但没给自定义 matcher 时，自动生成「整条消息精确等于任一触发词」的匹配器，
            # 避免出现永远无法触发的情况
            _kws = set(keywords or [])
            m = lambda t: (t or "").strip() in _kws
        func.matcher = m
        _COMMANDS.append(func)
        return func
    return decorator


# 未实装功能（菜单里展示占位）
UNIMPLEMENTED = [
    ("meme", "制作各种表情包呢喵"),
    ("占位符喵", ""),
    ("占位符喵", ""),
]


def menu_text():
    """生成精简的功能菜单文本（固定版式）。"""
    return "\n".join([
        "🐱 功能菜单",
        "· 📺B站解析",
        "· 😛meme",
        "· 🐷每日小猪",
        "· 🖼️随机图片",
        "· 📦️其他功能",
    ])


async def dispatch(ctx):
    """把收到的消息路由到对应命令函数。"""
    import re

    # 防重复：同一消息 id 短时间内只处理一次（WebSocket 与 Webhook 可能同时收到同一条）
    msg_id = getattr(ctx.message, "id", None)
    if msg_id:
        now = time.time()
        if _RECENT_IDS.get(msg_id, 0) > now - _DEDUP_WINDOW:
            return
        _RECENT_IDS[msg_id] = now
        if len(_RECENT_IDS) > 200:
            for k in [k for k, v in _RECENT_IDS.items() if now - v > _DEDUP_WINDOW]:
                _RECENT_IDS.pop(k, None)

    text = (getattr(ctx.message, "content", None) or "").strip()
    # 统一去掉 @ 机器人前缀（形如 <@xxxx> 你好）和快捷指令的 / 前缀（形如 /你好）
    text = re.sub(r"^<@[^>]*>\s*", "", text).strip()
    text = re.sub(r"^/\s*", "", text).strip()

    # 空内容（只 @机器人 不带字）或完全匹配菜单词 -> 显示功能菜单
    if not text or text in MENU_KEYWORDS:
        await ctx.reply(menu_text())
        return

    # 先检查自定义匹配器（如「消息含 BV 号就触发」）
    for func in _COMMANDS:
        if not state.is_enabled(func.__name__):
            continue
        if not _role_ok(func, ctx.openid):
            continue
        if not _group_allowed(func, ctx):
            continue  # 该命令在群里被禁用只跳过它，不影响后续命令
        matcher = getattr(func, "matcher", None)
        if matcher and matcher(text):
            # 被动解析模式（如 B站解析）：群里没 @ 机器人时，仅「消息里确实带 B站内容
            # （如 BV 号）」的自动解析被跳过，让消息落到多平台 B站/其他命令；
            # 显式命令词（如「B站解析」看教程）不受被动模式限制。
            if (
                getattr(func, "passive_gate", False)
                and state.get_bilibili_mode() == "passive"
                and ctx.scene == "group"
                and not getattr(ctx.message, "at_me", False)
                and (text or "").strip().lower()
                not in {k.lower() for k in (func.keywords or [])}
            ):
                continue
            ctx.keyword = func.keywords[0] if func.keywords else ""
            ctx.args = text
            await func(ctx)
            return

    # 依次匹配已注册命令的触发词（找到第一个命中的执行）
    for func in _COMMANDS:
        if not state.is_enabled(func.__name__):
            continue
        if getattr(func, "exact", False):
            continue  # 精确触发命令只走上面的 matcher，避免「· 抽猪」「我要抽猪」子串误触发
        if not _role_ok(func, ctx.openid):
            continue
        if not _group_allowed(func, ctx):
            continue  # 该命令在群里被禁用只跳过它，不影响后续命令
        for kw in func.keywords:
            if kw in text:
                ctx.keyword = kw
                ctx.args = text.split(kw, 1)[1].strip()
                await func(ctx)
                return

    # 多平台解析（B站小程序卡片/抖音/快手/A站/网易云等）：
    # 所有已注册命令都没命中后才尝试，避免与现有命令重复回复。独立开关见 state['parse']。
    try:
        if state.is_enabled("parse_enabled"):
            from bot.parse import gateway
            if await gateway.engine().handle(ctx):
                return
    except Exception:
        pass

    # AI 兜底：其他所有命令都没命中时，@机器人（群）或私聊的普通消息交给 AI 接入聊
    try:
        from bot.ai import ai
        if await ai.handle_candidate(ctx, text):
            return
    except Exception:
        pass

    # 未识别：群聊里闲聊极易误触发，静默以避免打扰；私聊仍给提示
    if ctx.scene != "group":
        await ctx.reply("没听懂哦，试试发「菜单」看我能做什么。\n" + menu_text())


# 导入并加载所有命令模块（保证它们的 @register 被执行）
from . import hello  # noqa: E402,F401
from . import bilibili  # noqa: E402,F401
from . import meme  # noqa: E402,F401
from . import randomimg  # noqa: E402,F401
from . import searchimg  # noqa: E402,F401