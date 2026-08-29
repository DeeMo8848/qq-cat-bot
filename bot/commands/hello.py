# -*- coding: utf-8 -*-
"""「你好」命令：验证机器人存活（仅 bot 管理员可触发）。"""

from . import register, ROLE_ADMIN


@register(keywords=["你好", "你好呀", "在吗", "hi", "hello", "ping"], help="打个招呼，确认我还活着", role=ROLE_ADMIN)
async def cmd_hello(ctx):
    await ctx.reply("活着呢")