# -*- coding: utf-8 -*-
"""🎴 影之诗制卡器（sv_card）

引用一张图片，配触发词即可合成卡牌：

* **单卡图** —— `sv卡牌` / `影之诗卡牌` 等（输出 782×1024 官方卡框）
* **效果图** —— `sv卡牌效果图` / `影之诗效果图` 等（输出 1920×1080，含正文/进化/纹章区）

渲染内核直接复用「影之诗制卡器-官方开源版」的 wasm 产物（**只读引用，不修改原项目**），
资源已复制到本插件 `assets/` 下，可独立部署。

参数与默认值见 `config_schema.py`，文本解析见 `parser.py`。
"""

from . import commands  # noqa: F401  命令注册
from .commands import SV_CMD_NAMES as CMD_NAMES  # noqa: F401  Web 后台模块开关用
