# -*- coding: utf-8 -*-
"""🎮 游戏娱乐模块。

移植自 astrbot 插件（见各文件头部注释）：
  · star.py  —— 《吉星派对》随机地图角色抽取（astrbot_plugin_astralparty）
  · blackjack.py —— 群聊 21 点小游戏（astrbot_plugin_blackjack21）
"""

from . import menu  # noqa: F401  保证 @register 被执行
from . import star  # noqa: F401
from . import blackjack  # noqa: F401
from plugins.turtlesoup import TURTLE_CMD_NAMES  # 海龟汤归入「游戏娱乐」模块

# 供 Web 后台「游戏娱乐」模块的 功能分组：每个功能整体一个开关，不逐条展开命令。
# 各元素：(group_key, 显示名, [命令名...])
GAME_GROUPS = [
    (
        "game_astral",
        "吉星派对随机",
        ["cmd_star_character", "cmd_star_map", "cmd_star_team", "cmd_star_random"],
    ),
    (
        "game_bj",
        "21点",
        [
            "cmd_bj_help", "cmd_bj_open", "cmd_bj_join", "cmd_bj_deal",
            "cmd_bj_hit", "cmd_bj_stand", "cmd_bj_status", "cmd_bj_end",
        ],
    ),
    (
        "turtle_soup",
        "海龟汤",
        sorted(TURTLE_CMD_NAMES),
    ),
]

# 归属「游戏娱乐」模块的所有命令（含入口命令），用于隐藏与一键开关
GAME_CMD_NAMES = {n for _, _, ns in GAME_GROUPS for n in ns} | {"cmd_game_menu"}
