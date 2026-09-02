# -*- coding: utf-8 -*-
"""🎮 游戏娱乐模块入口：发「游戏娱乐」返回可用游戏列表。"""

from bot.commands import register, ROLE_ALL


@register(keywords=["游戏娱乐"], help="🎮游戏列表", role=ROLE_ALL, exact=True)
async def cmd_game_menu(ctx):
    await ctx.reply_text(
        "🎮 游戏娱乐\n"
        "· 星趴角色 / 星趴地图 / 星趴队伍 / 星趴随机 — 吉星派对人物地图抽取\n\n"
        "🃏 21点（群聊，每人一间房）：\n"
        "· 创建21点 — 开房间\n"
        "· 加入21点 — 加入\n"
        "· 开始21点 — 管理员开局（至少2人）\n"
        "· 要牌 / 停牌 — 同步回合操作\n"
        "· 21点状态 / 21点帮助 / 结束21点\n\n"
        "🍲 海龟汤推理：\n"
        "· 开始海龟汤 — 来一场离奇谜题推理喵\n\n"
        "发送 /菜单 可回到主菜单喵"
    )