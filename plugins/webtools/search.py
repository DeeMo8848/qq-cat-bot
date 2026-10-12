# -*- coding: utf-8 -*-
"""DeepSeek 官方原生联网搜索（Responses API + 服务端 web_search 工具）。

不用任何第三方搜索 API（Tavily / 博查 / SerpAPI…），直接用 bot 已有的 DeepSeek key。
搜索在**服务端执行**，我们只负责发请求 + 取回正文与来源，不需要自己爬网页、
也不需要实现 tool-calling 循环。

★★ 实测结论（2026-10-13 在服务器上逐条验证，官方文档与实测不一致的地方都写在这）：

1. 工具类型必须写 ``web_search_2025_08_26``；写成 ``web_search`` **不会触发搜索**
   （官方文档说两种都支持，实测只有带日期的那种真去搜）。
2. 只有 ``deepseek-v4-pro`` 能搜；``deepseek-v4-flash`` 会返回 ``status=incomplete``、
   0 次搜索、正文为空（所以默认模型是 pro，别改成 flash）。
3. 搜索动作以 ``web_search_call`` 项返回：``action.type`` 为 ``search`` / ``open_page``，
   后者带 ``action.url`` —— 这就是来源链接。
4. REST 响应里**没有** ``output_text`` 字段（那是 OpenAI SDK 的便捷属性，
   官方示例用的是 SDK）→ 正文要从 ``output[].content[].text``（``type=output_text``）里取。
5. 成本差异极大：不给约束时模型会反复 open_page，
   一次搜索实测烧掉 **5.2 万 token / 33 秒**；加一句「只做一次搜索、不要反复打开网页」
   后降到 **约 2900 token / 3 秒**（约 1/18）。⇒ 默认提示词必须带这条约束。
"""

import asyncio
import json
import logging
import time

import aiohttp

from config import (WEBSEARCH_CACHE_TTL, WEBSEARCH_COOLDOWN,
                    WEBSEARCH_MAX_OUTPUT_TOKENS, WEBSEARCH_MODEL,
                    WEBSEARCH_TIMEOUT)

_log = logging.getLogger("webtools.search")

TOOL_TYPE = "web_search_2025_08_26"

# 收紧约束：避免模型反复 open_page 把 token 烧成几十倍（实测见模块注释第 5 条）
_SYS_PROMPT = (
    "你在帮 QQ 群友查最新资料。只做一次搜索，不要反复打开网页，直接给出结论。\n"
    "输出要求：中文；纯文本（不要星号加粗、不要表格、不要代码块）；300 字以内；"
    "只讲搜索结果里能确认的信息，拿不准就直说拿不准；"
    "最后用 1-3 行「🔗 链接」列出你实际参考的来源。"
)

# 查询缓存与冷却：搜索要花钱，同一问题短时间内不要重复烧 token
_CACHE = {}          # {query_key: (ts, result)}
_COOLDOWN = {}       # {scope: ts}


def _cfg_int(v, dflt):
    try:
        return int(v)
    except Exception:
        return dflt


def cached(query, scope=None):
    """命中缓存则返回结果，否则 None；同时做每会话冷却检查。

    返回 (hit: bool, result_or_None, err_msg_or_None)：
    hit=True 时用 result；hit=False 且 err 非空表示触发冷却（应提示用户稍等）。
    """
    now = time.time()
    ttl = _cfg_int(WEBSEARCH_CACHE_TTL, 600)
    key = " ".join((query or "").split()).lower()
    got = _CACHE.get(key)
    if got and now - got[0] < ttl:
        return True, got[1], None
    if scope:
        last = _COOLDOWN.get(scope)
        cd = _cfg_int(WEBSEARCH_COOLDOWN, 5)
        if last and now - last < cd:
            return False, None, "刚搜过一次喵，等 %d 秒再来～" % int(cd - (now - last) + 1)
    return False, None, None


def remember(query, result, scope=None):
    key = " ".join((query or "").split()).lower()
    _CACHE[key] = (time.time(), result)
    if scope:
        _COOLDOWN[scope] = time.time()
    # 简单清理，防止长期运行无限增长
    now = time.time()
    ttl = _cfg_int(WEBSEARCH_CACHE_TTL, 600)
    for k in [k for k, v in _CACHE.items() if now - v[0] > ttl * 3]:
        _CACHE.pop(k, None)
    for k in [k for k, v in _COOLDOWN.items() if now - v > 60]:
        _COOLDOWN.pop(k, None)


def _extract(resp):
    """从 Responses 响应里取正文 / 来源 / 用量（结构见模块注释第 3、4 条）。"""
    texts, sources, queries, usage = [], [], [], {}
    for item in (resp.get("output") or []):
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "message":
            for c in (item.get("content") or []):
                if not isinstance(c, dict):
                    continue
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    texts.append(c["text"])
        elif t == "web_search_call":
            act = item.get("action") or {}
            if act.get("type") == "open_page" and act.get("url"):
                sources.append(act["url"])
            for q in (act.get("queries") or []):
                if isinstance(q, str) and q.startswith("ws_call_id=") is False:
                    queries.append(q)
    u = resp.get("usage") or {}
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        if k in u:
            usage[k] = u.get(k)
    text = "\n".join(x.strip() for x in texts if x and x.strip()).strip()
    # 去重保序
    seen, uniq = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return {"text": text, "sources": uniq, "queries": queries, "usage": usage,
            "status": resp.get("status")}


async def search(query, timeout=None):
    """联网搜索。成功返回 dict（text/sources/queries/usage），失败抛 RuntimeError。"""
    q = (query or "").strip()
    if not q:
        raise RuntimeError("搜索内容为空")
    if len(q) > 200:
        q = q[:200]

    from bot.ai import ai as ai_mod
    cfg = await ai_mod.get_config()
    provider = str(cfg.get("provider") or "").lower()
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    key = str(cfg.get("api_key") or "")
    if not key:
        raise RuntimeError("还没配置 AI 的 API Key，联网搜索用不了喵")
    # 走的是 DeepSeek 的 Responses API + 官方 web_search 工具，换别家/自建端点就没有这个能力
    if not base:
        base = "https://api.deepseek.com" if provider == "deepseek" else ""
    if not base or "deepseek" not in base:
        raise RuntimeError("当前 AI 服务商不是 DeepSeek，官方联网搜索用不了喵")

    model = WEBSEARCH_MODEL or "deepseek-v4-pro"
    if "pro" not in model.lower():
        # 不是为了拦，是为了让「改配置想省钱」的人能在日志里看到原因：
        # flash 不是便宜版搜索，是根本搜不出来（status=incomplete、正文空）。
        _log.warning("[webtools] WEBSEARCH_MODEL=%s —— 实测只有 deepseek-v4-pro 能触发联网搜索，"
                     "flash 会返回 incomplete 且正文为空", model)
    body = {
        "model": model,
        "instructions": _SYS_PROMPT,
        "input": q,
        "tools": [{"type": TOOL_TYPE}],
        "max_output_tokens": _cfg_int(WEBSEARCH_MAX_OUTPUT_TOKENS, 700),
    }
    url = base + "/responses"
    to = _cfg_int(timeout or WEBSEARCH_TIMEOUT, 120)
    t0 = time.time()
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=to)) as s:
            async with s.post(url, json=body,
                              headers={"Content-Type": "application/json",
                                       "Authorization": "Bearer " + key}) as r:
                raw = await r.text()
                if r.status not in (200, 201):
                    raise RuntimeError("搜索接口 HTTP %s：%s" % (r.status, _short(raw)))
        try:
            data = json.loads(raw)
        except Exception:
            raise RuntimeError("搜索返回的不是 JSON：%s" % _short(raw))
        out = _extract(data)
    except RuntimeError:
        raise
    except asyncio.TimeoutError:
        raise RuntimeError("搜索超时（%ds）喵，换个问法或稍后再试" % to)
    except Exception as e:
        raise RuntimeError("搜索失败：%s: %s" % (type(e).__name__, _short(str(e))))

    text = out["text"]
    # 服务端偶发把安全分类结果当正文返回（实测 flash 出现过），直接当失败处理
    if text.startswith("<ds_safety>") or "<ds_safety>" in text[:60]:
        raise RuntimeError("搜索没有返回有效内容喵，换条更具体的问法试试")
    if not text:
        hint = "（模型没输出内容"
        if out.get("status") == "incomplete":
            hint += "，可能是 max_output_tokens 太小"
        raise RuntimeError("搜索没拿到答案喵%s）" % hint)

    # 正文里没带链接时，用服务端返回的 open_page 地址补上来源
    if "http" not in text and out["sources"]:
        text += "\n\n🔗 来源：\n" + "\n".join(out["sources"][:3])

    # ★ 打模型名：搜索与「AI 聊天」用的是两套模型（搜索固定 pro，聊天用后台里设的那个，
    #   通常是 flash），出问题时一眼能看出是哪套在花钱。
    print("[webtools] 联网搜索完成：%.1fs model=%s tokens=%s 来源=%d｜%s"
          % (time.time() - t0, model, out["usage"].get("total_tokens"),
             len(out["sources"]), q[:40]),
          flush=True)
    return {"text": text, "sources": out["sources"], "usage": out["usage"]}


def _short(text, n=200):
    return " ".join(str(text or "").split())[:n]
