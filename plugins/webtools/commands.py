# -*- coding: utf-8 -*-
"""联网搜索 / 链接总结 两条命令。

触发词设计（与用户约定一致）：
* **联网搜索**：触发词必须出现在**消息开头**且紧跟词边界（同 sv_card / 海龟汤 的做法）
  —— 因为命令后面要跟查询内容，不能用 exact（整条相等会让「搜索 xxx」失效）。
* **链接总结**：用**模糊匹配**（子串），因为用户会写成「呆猫帮我总结下这个」，
  触发词不一定在开头。为防闲聊误触发，触发词一律取长（≥4 字），
  且必须**消息里能拿到链接**才真正执行；拿不到链接时只有 @ 了机器人才回提示，
  否则保持安静。

★★ @ 门槛（用户 2026-10-13 要求）：**群里必须 @ 机器人才会执行，不 @ 完全静默。**
两条命令都要 —— 它们都会烧 token（搜索约 3k/次、总结也走一次 AI），
群里随手一句「搜索一下」「帮我看看这个」就触发既费钱又容易刷屏。
单聊（c2c）不存在 @ 的概念、也不会打扰别人 ⇒ 一律放行。
★ 副作用要记住：dispatch 命中的命令会「消费」掉这条消息，
所以不 @ 时是【彻底没反应】，不会 fallback 到后面的命令或 AI 兜底。
"""

import logging

from bot.commands import ROLE_ALL, register

from . import search as search_mod
from . import summary as summary_mod

_log = logging.getLogger("webtools.commands")

# 供 Web 后台「插件总开关」使用的命令名集合（名字必须与 webui 的导入一致）
WEBTOOLS_CMD_NAMES = {"cmd_web_search", "cmd_link_summary"}

# ---- 触发词 ----
SEARCH_TRIGGERS = ["联网搜索", "联网搜", "帮我搜索一下", "帮我搜索", "帮我搜一下",
                   "帮我搜", "搜索", "搜一下"]
# 模糊匹配但也够长：不会因为「总结」两个字就误触发
SUMMARY_TRIGGERS = [
    "帮我总结", "帮我总结下", "帮我总结一下", "总结一下这个",
    "帮我概括", "帮我看看这个", "帮我读一下", "帮我分析这个",
]

_BOUNDARY = set(" \t\r\n，,。.、；;：:！!？?～~*#-—_/|\\\"'（(【[「")


def _prefix_trigger(text, triggers):
    """触发词在开头；短触发词（<4 字）还要求紧跟词边界。

    为什么要分开处理：中文习惯常常不加空格（「帮我搜一下天安门开放时间」），
    如果一律要求词边界，这类写法就用不了。而「搜索」只有 2 字，
    出现在「搜索功能挺难用的」这种句子里就会被误触发 —— 所以要边界。
    长的触发词（≥4 字，如「帮我搜一下」）本身足够具体，直接接查询词即可。
    （触发词必须在**开头**，所以句中出现的「帮我搜一下」本来就不会命中。）
    """
    t = (text or "").strip()
    tl = t.lower()
    for kw in sorted(triggers, key=len, reverse=True):
        k = kw.lower()
        if not tl.startswith(k):
            continue
        rest = t[len(kw):]
        if len(kw) >= 4 or not rest or rest[0] in _BOUNDARY:
            return kw
    return ""


def _match_search(text):
    return bool(_prefix_trigger(text, SEARCH_TRIGGERS))


def _match_summary(text):
    t = text or ""
    return any(kw in t for kw in SUMMARY_TRIGGERS)


def _text_of(ctx):
    return (getattr(ctx, "args", None)
            or getattr(ctx.message, "content", "") or "").strip()


def _scope(ctx):
    return "%s:%s:%s" % (ctx.scene, ctx.target, ctx.openid)


def _at_ok(ctx):
    """群里是否通过了「@ 机器人」这道门槛（见模块注释里的 ★★ @ 门槛）。

    单聊没有 @ 的概念，直接放行；群聊必须 at_me=True。
    """
    if ctx.scene != "group":
        return True
    return bool(getattr(ctx.message, "at_me", False))


def _can_tip(ctx):
    """能不能回「没看到链接」这类提示。

    群聊要 @ 才回（否则群里有人抱怨一句就冒出来刷屏）；
    单聊本来就是冲着机器人说的，直接回。
    """
    if ctx.scene != "group":
        return True
    return bool(getattr(ctx.message, "at_me", False))


@register(keywords=SEARCH_TRIGGERS,
          help="🌐 联网搜索：@我 + 搜索 xxx（DeepSeek 官方联网，不用第三方搜索 key）",
          matcher=_match_search, role=ROLE_ALL)
async def cmd_web_search(ctx):
    if not _at_ok(ctx):
        return                      # 群里没 @：静默（见模块注释 ★★ @ 门槛）
    text = _text_of(ctx)
    kw = _prefix_trigger(text, SEARCH_TRIGGERS)
    query = text[len(kw):].strip(" ：:，,。-—_") if kw else text
    if not query:
        await ctx.reply_text(
            "🌐 联网搜索\n"
            "用法：@我 搜索 今天的科技新闻\n"
            "· 群里要 @ 我才触发（搜索要烧 token，怕被随手刷）\n"
            "· 走 DeepSeek 官方联网搜索，只搜一次，结果缓存 10 分钟\n"
            "· 会消耗 AI token（约 3k/次），别当搜索栏狂刷喵")
        return

    scope = _scope(ctx)
    hit, cached, err = search_mod.cached(query, scope)
    if hit and cached:
        await ctx.reply_text(cached["text"], reply=True)
        return
    if err:
        await ctx.reply_text(err)
        return

    try:
        await ctx.reply_text("🔍 正在联网搜索喵…（约 3~15 秒）")
    except Exception:
        pass

    try:
        res = await search_mod.search(query)
    except Exception as e:
        _log.warning("[webtools] 搜索失败: %s", e)
        await ctx.reply_text("搜索失败喵：%s" % e)
        return

    search_mod.remember(query, res, scope)
    await ctx.reply_text(res["text"], reply=True)


@register(keywords=SUMMARY_TRIGGERS,
          help="📄 链接总结：@我 + 引用一条带链接的消息，说「帮我总结下这个」",
          matcher=_match_summary, role=ROLE_ALL)
async def cmd_link_summary(ctx):
    if not _at_ok(ctx):
        return                      # 同上：群里没 @ 不读网页、不烧 token
    text = _text_of(ctx)
    urls = summary_mod.collect_urls(ctx.message, text)
    if not urls:
        # 只是随口说了「帮我总结」但没给链接：@ 了机器人才提示，避免群里刷屏
        if _can_tip(ctx):
            await ctx.reply_text(
                "没看到链接喵～\n"
                "引用一条带链接的消息，再说「帮我总结下这个」就行；"
                "也可以直接把链接和这句话发在同一条里")
        return

    try:
        await ctx.reply_text("📄 正在读这个网页喵…（约 5~20 秒）")
    except Exception:
        pass

    last_err = None
    for url in urls[:2]:                 # 第一条失败时再试一条，提高成功率
        if not await summary_mod.is_public_url(url):
            last_err = "这个地址不能访问喵（内网/本机地址会被拦）"
            continue
        try:
            out = await summary_mod.summarize(url)
        except Exception as e:
            last_err = str(e)
            _log.warning("[webtools] 链接总结失败 %s: %s", url, e)
            continue
        await ctx.reply_text(out, reply=True)
        return

    await ctx.reply_text("总结失败喵：%s" % (last_err or "未知原因"))
