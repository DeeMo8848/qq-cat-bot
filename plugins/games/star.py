# -*- coding: utf-8 -*-
"""《吉星派对》随机地图角色抽取。

移植自 astrbot 插件 astrbot_plugin_astralparty（协议 MIT）。
纯随机抽取，无状态、无网络依赖，任何人都可触发。
"""

import random

from bot.commands import register, ROLE_ALL

# 角色清单
_CHARACTERS = [
    "太刀", "风水", "猫猫", "蒸蛋", "蓝海晴", "女王", "真梦梓", "小狐狸",
    "茉莉", "凛", "邦妮", "叔叔", "旗袍", "修女", "熊猫", "多萝西",
    "鼠鼠", "米米", "忍者", "美甲师", "史莱姆", "摩西", "吸血鬼", "吉尔",
    "阿尔", "摩托", "墨影", "老板娘", "垃圾桶", "超天酱", "糖糖", "玲玲",
]

# 地图清单
_MAPS = [
    "水乡古镇", "幽魂暗巷", "魔法学院", "园林中庭",
    "星趴·梦想号", "御魂庆典", "龙宫游乐园",
]


@register(keywords=["星趴角色"], help="随机抽取一个吉星角色喵", role=ROLE_ALL, exact=True)
async def cmd_star_character(ctx):
    await ctx.reply_text(f"你抽取到的角色是：{random.choice(_CHARACTERS)}")


@register(keywords=["星趴地图"], help="随机抽取一张吉星地图喵", role=ROLE_ALL, exact=True)
async def cmd_star_map(ctx):
    await ctx.reply_text(f"你抽取到的地图是：{random.choice(_MAPS)}")


@register(keywords=["星趴队伍"], help="随机抽取一组吉星队伍喵", role=ROLE_ALL, exact=True)
async def cmd_star_team(ctx):
    team = random.sample(_CHARACTERS, 4)
    await ctx.reply_text("你抽取到的队伍是：\n" + "\n".join(team))


@register(keywords=["星趴随机"], help="随机吉星地图和队伍喵", role=ROLE_ALL, exact=True)
async def cmd_star_random(ctx):
    team = random.sample(_CHARACTERS, 4)
    lines = [f"你抽取到的地图是：{random.choice(_MAPS)}", "队伍："]
    lines.extend(f" {c}" for c in team)
    await ctx.reply_text("\n".join(lines))