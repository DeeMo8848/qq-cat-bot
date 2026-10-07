# -*- coding: utf-8 -*-
"""Markdown / 按钮能力实测插件（入口）。

★ 新增插件必须去 `bot/commands/__init__.py` **末尾的加载清单**里加一行
  `from plugins import md_test` —— 本项目**没有任何自动扫描机制**
  （`plugins/__init__.py` 是空文件），漏了那一行插件会完全不生效，且日志毫无痕迹。
"""

from . import commands  # noqa: F401  触发 @register
from .commands import MD_CMD_NAMES  # noqa: F401

__all__ = ["MD_CMD_NAMES"]
