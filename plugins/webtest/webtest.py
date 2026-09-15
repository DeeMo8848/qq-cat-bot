# -*- coding: utf-8 -*-
"""网页测试：发送公网页面链接，验证静态服务 + 隧道连通。

页面由 bot/core/static_server.py 提供（仅开放 bot/public_html/ 目录），
经 cloudflared 隧道域名 page.deemo8848.dpdns.org 公开访问。
"""

from bot.commands import register, ROLE_ALL
from config import STATIC_PUBLIC_URL


def _exact(kw):
    return lambda t: (t or "").strip() == kw


@register(keywords=["网页测试"], help="测试：返回一个公网可访问的网页链接喵",
          matcher=_exact("网页测试"), role=ROLE_ALL, exact=True)
async def cmd_webtest(ctx):
    await ctx.reply(f"点我打开测试页喵 👉 {STATIC_PUBLIC_URL}")


# web 后台命令列表自动展示（无独立模块分组，保持独立开关）
WEBTEST_CMD_NAMES = {"cmd_webtest"}
