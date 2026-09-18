# -*- coding: utf-8 -*-
"""🎮 游戏娱乐模块入口：发「游戏娱乐」返回可用游戏列表，发送对应名称即可展开详细命令。"""

from bot.commands import register, ROLE_ALL


@register(keywords=["游戏娱乐"], help="🎮游戏列表", role=ROLE_ALL, exact=True)
async def cmd_game_menu(ctx):
    await ctx.reply_text(
        "🎮 游戏娱乐—发送对应名称查看详细命令\n"
        "· 随机星趴\n"
        "· 21点\n"
        "· 海龟汤\n"
        "· 博彩游戏\n"
        "· 钓鱼系统\n"
        "· 卡牌制作"
    )


@register(keywords=["博彩游戏"], help="🎲 博彩游戏详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_gamble(ctx):
    await ctx.reply_text(
        "🎲 博彩游戏（小赌怡情）：\n"
        "· 骰宝 <类型> <金额> — 大/小/单/双/豹子/点数(4-17)，即时结算\n"
        "· 命运之轮 <金额> — 10 层挑战，成功率逐层递减，可继续挑战\n"
        "· 擦弹 <金额> / 擦弹 allin / 擦弹 halfin — 随机倍率\n\n"
        "发送「游戏娱乐」回到列表喵"
    )


@register(keywords=["随机星趴"], help="🎲 吉星派对详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_star(ctx):
    await ctx.reply_text(
        "🎲 吉星派对—人物地图抽取\n"
        "· 星趴角色 — 随机一位吉星角色\n"
        "· 星趴地图 — 随机一张吉星地图\n"
        "· 星趴队伍 — 随机一组吉星队伍\n"
        "· 星趴随机 — 地图 + 队伍一起随机\n\n"
        "发送「游戏娱乐」回到列表喵"
    )


@register(keywords=["21点"], help="🃏 21点详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_blackjack(ctx):
    await ctx.reply_text(
        "🃏 21点（群聊，每人一间房）：\n"
        "· 创建21点 — 开房间\n"
        "· 加入21点 — 加入\n"
        "· 开始21点 — 管理员开局（至少2人）\n"
        "· 要牌 / 停牌 — 同步回合操作\n"
        "· 21点状态 / 21点帮助 / 结束21点\n\n"
        "发送「游戏娱乐」回到列表喵"
    )


@register(keywords=["海龟汤"], help="🍲 海龟汤详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_turtle(ctx):
    await ctx.reply_text(
        "🍲 海龟汤推理：\n"
        "· 开始海龟汤 — 来一场离奇谜题推理\n"
        "· 海龟汤提问 — 汤中提问\n"
        "· 题库列表 — 查看海龟汤题库\n"
        "· 题目详情 — 查看单题详解\n"
        "· 海龟汤帮助 / 结束海龟汤 / 公布答案 / 换一题\n\n"
        "发送「游戏娱乐」回到列表喵"
    )


@register(keywords=["钓鱼系统"], help="🐟 钓鱼系统详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_fishing(ctx):
    await ctx.reply_text(
        "🐟 钓鱼系统：\n"
        "· 开始钓鱼 — 抛一竿（自动钓鱼：自动钓鱼 / 停止自动钓鱼）\n"
        "· 我的鱼获 / 我的渔具箱 — 查看鱼获与渔具\n"
        "· 鱼具店 / 买鱼竿 — 购买与装备渔具\n"
        "· 卖鱼 / 一键卖鱼 — 卖鱼获赚喵喵币\n"
        "· 扭蛋 / 鱼获图鉴 / 钓鱼成就 / 钓鱼称号\n"
        "· 鱼缸 / 存鱼 / 取鱼 — 鱼缸观赏防偷\n"
        "· 偷鱼 / 电鱼 — 偷取他人鱼缸的鱼（高风险）\n"
        "· 排行榜 — 喵喵币财富榜\n"
        "· 鱼市 / 挂鱼 / 买鱼 / 撤单 — 玩家交易\n"
        "· 交易所 / 持仓 — 大宗商品投资\n"
        "· 转账 / 发红包 / 领红包 / 红包列表 — 社交\n\n"
        "发送「游戏娱乐」回到列表喵"
    )


@register(keywords=["卡牌制作"], help="🃏 卡牌制作详细命令", role=ROLE_ALL, exact=True)
async def cmd_game_menu_card(ctx):
    await ctx.reply_text(
        "🃏 卡牌制作：\n"
        "· 卡牌制作 <卡名>（引用图片）— 直接制作\n"
        "· 制作卡牌 / 卡牌帮助 — 制作教程\n"
        "· 收集册 / 我的卡牌 — 查看我的卡牌\n"
        "· 卡牌市场 / 购买卡牌 — 商店买卡\n"
        "· 上架卡牌 / 下架卡牌 — 市场挂单出售\n"
        "· 销毁卡牌 — 销毁（无补偿）\n"
        "· 素材包 / 购买背景 名称 / 购买辉光 名称… — 卡牌素材\n"
        "· 导出卡牌 <卡名> 为图片 / 为网页 — 导出卡牌文件\n\n"
        "发送「游戏娱乐」回到列表喵"
    )