# -*- coding: utf-8 -*-
"""🌐 联网 / 网页工具（webtools）

两条命令，都建立在「bot 已有的 AI 配置」之上，**不需要任何第三方搜索 API key**：

* ``搜索 xxx`` —— DeepSeek 官方原生联网搜索（Responses API + 服务端 ``web_search`` 工具），
  见 ``search.py`` 顶部注释：里面记了实测出来的三个坑（工具类型要带日期、
  只有 pro 模型能搜、收紧提示词能把 token 从 5 万压到 3 千）。
* 引用一条带链接的消息 + 「帮我总结下这个」 —— 抓正文后交给 AI 出摘要，
  见 ``summary.py``（本地 bs4+lxml 解析，JS 页面再走 r.jina.ai 兜底）。

依赖：aiohttp（已有）、beautifulsoup4 + lxml（服务器已装）；**无需 Playwright**。
"""

from . import commands  # noqa: F401  命令注册
# ★ 必须导出 `WEBTOOLS_CMD_NAMES` 这个名字：bot/core/webui.py 的 _module_groups() 按此名导入
#   （写错名字会让 /api/status 抛 ImportError → 后台整个面板 500「检测中…」）
from .commands import WEBTOOLS_CMD_NAMES  # noqa: F401  Web 后台模块开关用

__all__ = ["WEBTOOLS_CMD_NAMES"]
