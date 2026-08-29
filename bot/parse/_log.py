# -*- coding: utf-8 -*-
"""本模块被解析插件 core 引用（原插件用 astrbot 的 logger），提供一个等价对象。"""
import logging

_log = logging.getLogger("parse")


class _Logger:
    def debug(self, msg, *a, **k):
        _log.debug(msg, *a, **k)

    def info(self, msg, *a, **k):
        _log.info(msg, *a, **k)

    def warning(self, msg, *a, **k):
        _log.warning(msg, *a, **k)

    def error(self, msg, *a, **k):
        _log.error(msg, *a, **k)

    def exception(self, msg, *a, **k):
        _log.exception(msg, *a, **k)

    def critical(self, msg, *a, **k):
        _log.critical(msg, *a, **k)


logger = _Logger()