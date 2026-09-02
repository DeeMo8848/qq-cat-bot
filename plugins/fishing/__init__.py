# -*- coding: utf-8 -*-
"""🐟 钓鱼游戏模块。玩法与命令见 game.py / core.py。
资金统一走 bot.core.wallet（喵喵币），实现跨插件资金互通。"""

from . import game      # noqa: F401  核心玩法函数
from . import core      # noqa: F401  命令注册
from . import gamble    # noqa: F401  赌博小游戏（骰宝/命运之轮/擦弹）
from . import social    # noqa: F401  社交互动（偷鱼/电鱼/水族箱）
from . import auto      # noqa: F401  自动钓鱼
from . import title     # noqa: F401  自动称号

# 归属「游戏娱乐」模块的所有钓鱼命令（函数名），用于 Web 后台一键开关/隐藏
FISHING_CMD_NAMES = {
    "cmd_fish", "cmd_inventory", "cmd_balance",
    "cmd_shop", "cmd_buy_rod", "cmd_buy_bait1", "cmd_buy_bait2",
    "cmd_sell", "cmd_sell_all", "cmd_rank", "cmd_codex", "cmd_gacha", "cmd_achievement",
    "cmd_market", "cmd_list_fish", "cmd_buy_fish", "cmd_cancel_order",
    "cmd_buy_hook", "cmd_buy_line", "cmd_buy_float", "cmd_equip", "cmd_enchant",
    "cmd_unenchant",
    "cmd_exchange", "cmd_holdings", "cmd_transfer",
    "cmd_send_redpack", "cmd_claim_redpack", "cmd_redpack_list",
    "cmd_sicbo", "cmd_wheel", "cmd_wheel_continue", "cmd_wheel_giveup", "cmd_eraser",
    "cmd_steal", "cmd_electric", "cmd_aquarium", "cmd_store_fish", "cmd_take_fish",
    "cmd_auto_fish", "cmd_stop_auto_fish", "cmd_title",
}
