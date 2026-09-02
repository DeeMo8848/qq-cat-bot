# -*- coding: utf-8 -*-
"""文本小功能：一言 / 名言 / 诗词（接口返回纯文本，直接回复）。

接口（南风城 API，纯文本）：
  · 随机一言  https://api.sretna.cn/api/aword/auto
  · 随机名言  https://api.sretna.cn/api/aword/mw
  · 随机诗词  https://api.sretna.cn/api/aword/sc
"""

import aiohttp

from bot.commands import register, ROLE_ALL

_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
}

_WORD_APIS = {
    "一言": "https://api.sretna.cn/api/aword/auto",
    "名言": "https://api.sretna.cn/api/aword/mw",
    "诗词": "https://api.sretna.cn/api/aword/sc",
}

_FAIL = "接口被猫叼走了喵，请稍后再试"


async def _fetch_text(url: str) -> str | None:
    try:
        async with aiohttp.ClientSession(headers=_HDRS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as r:
                if r.status != 200:
                    return None
                text = await r.text()
                return text.strip() or None
    except Exception:
        return None


def _exact(kw):
    return lambda t: (t or "").strip() == kw


async def _send_word(ctx, key: str):
    url = _WORD_APIS[key]
    text = await _fetch_text(url)
    if not text:
        await ctx.reply(_FAIL)
        return
    await ctx.reply(f"📜 {text}")


@register(keywords=["随机一言"], help="随机一句人生小语喵", matcher=_exact("随机一言"), role=ROLE_ALL, exact=True)
async def cmd_word_yiyan(ctx):
    await _send_word(ctx, "一言")


@register(keywords=["随机名言"], help="随机一句名言喵", matcher=_exact("随机名言"), role=ROLE_ALL, exact=True)
async def cmd_word_mingyan(ctx):
    await _send_word(ctx, "名言")


@register(keywords=["随机诗词"], help="随机一句诗词喵", matcher=_exact("随机诗词"), role=ROLE_ALL, exact=True)
async def cmd_word_shici(ctx):
    await _send_word(ctx, "诗词")


# web 后台「其他功能」模块分组用（见 bot/core/webui.py 的 _module_groups）
WORD_CMD_NAMES = {"cmd_word_yiyan", "cmd_word_mingyan", "cmd_word_shici"}