# -*- coding: utf-8 -*-
"""🃏 卡牌制作模块：制作卡牌 / 卡牌制作 / 收集册 / 我的卡牌 / 销毁卡牌 /
卡牌市场 / 上架卡牌 / 下架卡牌 / 购买卡牌 / 素材包 / 购买素材。

数据：data/cards/（卡牌记录 + 网页资产），资金走 bot.core.wallet（喵喵币）。
"""

from . import commands  # noqa: F401  命令注册
from . import forge      # noqa: F401  制作队列（worker 随首次入队启动）
from .commands import CARD_CMD_NAMES  # noqa: F401  Web 后台模块开关
