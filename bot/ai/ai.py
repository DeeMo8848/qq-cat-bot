# -*- coding: utf-8 -*-
"""AI 对话模块：接入多家大模型服务商（DeepSeek / 硅基流动 / OpenAI / Gemini /
Anthropic / 智谱 / Kimi / Groq / Mistral / Together / xAI / OpenRouter / 通义千问 …）。

设计要点：
- 服务商差异（认证方式、路径、请求体/响应体格式）全部收敛在 bot/ai/providers.py，
  本模块只负责「对话逻辑 + 配置 + 记忆」。
- 按 openid 隔离会话上下文（群 member_openid / 私聊 user_openid），各聊各的、互不串台。
- 记忆：cache/ai_memory.json 保存 AI 对每个用户的 {memory, summary}，
  由 AI 自己根据对话总结、程序只负责保存。新会话把记忆+总结注入 system prompt，
  因此即使换了会话，AI 拿到记忆后也能"变熟人"。
- 触发方式由命令分发层控制（@机器人、其他命令优先），本模块只负责"判断可用 + 生成回复"。
- 余额查询为尽力而为：不同服务商端点不同，查不到就返回 None（Web 显示"不支持/查询失败"）。
"""

import asyncio
import json
import logging
import os
import random
import time

from config import ROOT

from . import providers as _prov

_log = logging.getLogger("ai")

_CACHE_DIR = os.path.join(ROOT, "cache")
_CONFIG_FP = os.path.join(_CACHE_DIR, "ai_config.json")
_MEMORY_FP = os.path.join(_CACHE_DIR, "ai_memory.json")
_USAGE_FP = os.path.join(ROOT, "logs", "ai_usage.jsonl")

# 高峰时段提醒图（原 astrbot_plugin_fat_fish_wallet 的 assets，已复制到项目内）
PEAK_IMAGE = os.path.join(ROOT, "bot", "assets", "peak_guard", "高峰时段.png")
PEAK_NOTICE_LINES = [
    "高峰时段，钱包要紧，本喵休息中，空闲时段再喊本喵喵～",
    "梁文峰时间到，服务暂停，本喵先躲起来省钱喵！",
    "高峰时段费用太贵，本喵已下班，到点自动复活喵～",
]

_DEFAULT_PRESET = (
    "你是一只名叫『禄星』的黑猫，是群里普通的群友，和大家一起闲聊。"
    "说话自然、随便、有烟火气，可以带'喵'卖萌，就像真人群友那样，别摆 AI 架子、"
    "别用'作为一名AI'这类官方腔，回复简短些、像聊天。"
    "别人问正经问题，也用轻松的口气回答，别掉书袋。"
    "可以称呼发消息的人为 ta 的昵称。"
)

_DEFAULTS = {
    "enabled": False,
    "provider": "deepseek",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "system_preset": _DEFAULT_PRESET,
    "max_history": 12,          # 保留多少轮上下文
    "memory_interval": 0,       # 记忆总结间隔（轮）；0 = 关闭自动总结
    "temperature": 0.85,
    "max_tokens": 0,            # 0 = 不指定（用服务商默认）。Anthropic 必填，内部有兜底
    "reasoning_effort": "low",  # DeepSeek 思考强度：low/medium/high/max；low 最省 token
    "timeout": 90,              # 单次请求超时（秒）
}

# 每个用户在内存里的会话历史（openid -> [{"role","content"}, ...]）
_history = {}
# 正在总结中的 openid 集合，防止同一人并发触发多次总结
_summarizing = set()

_lock = asyncio.Lock()


# ---------- 持久化（同步小文件 IO，外面用锁串行） ----------
def _read_json(fp, default):
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(fp, data):
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 配置 ----------
def _random_reply_state():
    """随机回复当前状态（来自 settings.json，进程级配置）。"""
    from config import (AI_RANDOM_REPLY_ENABLED, AI_RANDOM_REPLY_PROBABILITY,
                        AI_RANDOM_REPLY_COOLDOWN)
    return {
        "enabled": bool(AI_RANDOM_REPLY_ENABLED),
        "probability": float(AI_RANDOM_REPLY_PROBABILITY or 0),   # 0~1
        "cooldown_min": int(max(0, AI_RANDOM_REPLY_COOLDOWN or 0) / 60),
    }


def _save_random_reply(enabled, probability, cooldown_min):
    """写回 settings.json 并热更新 config 模块常量，无需重启立即生效。"""
    import config as _cfg_mod
    fp = os.path.join(_cfg_mod.ROOT, "settings.json")
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    prob = max(0.0, min(1.0, float(probability or 0)))
    cd = max(0, int(cooldown_min or 0) * 60)
    data["AI_RANDOM_REPLY_ENABLED"] = bool(enabled)
    data["AI_RANDOM_REPLY_PROBABILITY"] = prob
    data["AI_RANDOM_REPLY_COOLDOWN"] = cd
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return False
    # 热更新模块常量与 _cfg 缓存：_random_reply_ok 每次调用重新 import config，
    # 更新常量即生效；同步 _CFG 让其他走 _cfg() 的读取一致。
    _cfg_mod.AI_RANDOM_REPLY_ENABLED = bool(enabled)
    _cfg_mod.AI_RANDOM_REPLY_PROBABILITY = prob
    _cfg_mod.AI_RANDOM_REPLY_COOLDOWN = cd
    _cfg_mod._CFG["AI_RANDOM_REPLY_ENABLED"] = bool(enabled)
    _cfg_mod._CFG["AI_RANDOM_REPLY_PROBABILITY"] = prob
    _cfg_mod._CFG["AI_RANDOM_REPLY_COOLDOWN"] = cd
    return True


async def get_config():
    async with _lock:
        cfg = dict(_DEFAULTS)
        cfg.update({k: v for k, v in _read_json(_CONFIG_FP, {}).items() if k in _DEFAULTS})
        cfg["random_reply"] = _random_reply_state()
        return cfg


async def save_config(data):
    """保存配置。切换服务商时自动带出该服务商的默认 base_url / model。

    规则（按优先级）：
      1. 用户**显式**改了 base_url / model → 用用户的值
      2. 否则若 provider 变了 → 用新服务商的 default_base / default_model
      3. 否则保留原值
    这样一个「切到 Gemini」的操作不会因为残留 deepseek 的地址而请求失败。
    """
    async with _lock:
        cfg = dict(_DEFAULTS)
        cur = _read_json(_CONFIG_FP, {})
        cfg.update({k: v for k, v in cur.items() if k in _DEFAULTS})
        old_provider = str(cfg.get("provider") or "")

        new_provider = str((data or {}).get("provider") or old_provider).strip().lower()
        provider_changed = bool(new_provider) and new_provider != old_provider

        payload = dict(data or {})
        # 判断用户是否显式改了这两个字段（空串视为「没改」，方便前端留空表示用默认）
        touched_base = bool(str(payload.get("base_url") or "").strip())
        touched_model = bool(str(payload.get("model") or "").strip())

        for k in _DEFAULTS:
            if k in payload and payload[k] not in (None, ""):
                cfg[k] = payload[k]

        cfg["provider"] = new_provider or old_provider or _DEFAULTS["provider"]
        meta = _prov.get(cfg["provider"])
        if provider_changed and not touched_base:
            cfg["base_url"] = meta.get("default_base", "")
        if provider_changed and not touched_model:
            cfg["model"] = meta.get("default_model", "")

        _write_json(_CONFIG_FP, cfg)

        # 随机触发回复：写 settings.json（进程级配置）并热更新，立即生效
        rr = payload.get("random_reply")
        if isinstance(rr, dict):
            _save_random_reply(
                bool(rr.get("enabled", _random_reply_state()["enabled"])),
                rr.get("probability", _random_reply_state()["probability"]),
                rr.get("cooldown_min", _random_reply_state()["cooldown_min"]),
            )
        cfg["random_reply"] = _random_reply_state()
        return cfg


async def is_enabled():
    cfg = await get_config()
    return bool(cfg["enabled"]) and bool(cfg["api_key"]) and bool(cfg["base_url"])


# ---------- 记忆 ----------
async def get_memory(openid):
    async with _lock:
        return (_read_json(_MEMORY_FP, {}).get(openid) or {})


async def all_memory():
    async with _lock:
        return _read_json(_MEMORY_FP, {})


async def save_memory(openid, nickname, memory, summary=""):
    """保存某人的长期记忆。只存两样：昵称 + 长期记忆 + 简短印象。
    画像/关系网等复杂维度已舍弃（2026-10-04 检修），省 token 也避免 AI 胡编。"""
    async with _lock:
        data = _read_json(_MEMORY_FP, {})
        old = data.get(openid) or {}
        data[openid] = {
            "nickname": nickname or old.get("nickname", ""),
            "memory": memory or old.get("memory", ""),
            "summary": summary or old.get("summary", ""),
            "updated": asyncio.get_event_loop().time(),
        }
        _write_json(_MEMORY_FP, data)
        return data[openid]


async def delete_memory(openid):
    async with _lock:
        data = _read_json(_MEMORY_FP, {})
        data.pop(openid, None)
        _write_json(_MEMORY_FP, data)
        return True


# ---------- 多服务商调用 ----------
def _log_usage(provider, model, msgs, reason, prompt_tokens, completion_tokens,
               ok, err="", source=""):
    """把一次 AI 调用追加到 logs/ai_usage.jsonl（一行一条，方便日后核对消耗）。"""
    try:
        from config import AI_USAGE_LOG_ENABLED
        if not AI_USAGE_LOG_ENABLED:
            return
        from datetime import datetime as _dt
        os.makedirs(os.path.dirname(_USAGE_FP), exist_ok=True)
        rec = {
            "ts": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "provider": provider,
            "model": model,
            "msgs": msgs,
            "reason": reason or "",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "ok": bool(ok),
            "err": err or "",
        }
        if source:
            rec["source"] = source
        with open(_USAGE_FP, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _call(cfg, messages, timeout=None):
    """按当前服务商配置发起一次对话请求，返回回复文本。

    服务商差异（认证头 / 路径 / 请求体 / 响应体）由 providers 模块处理，
    这里只负责发请求、记录用量日志与错误整形。
    """
    import aiohttp

    provider = str(cfg.get("provider") or "other").lower()
    base = str(cfg.get("base_url") or "").strip() or _prov.get(provider).get("default_base", "")
    if not base:
        raise RuntimeError("未配置 base_url（服务商 %s）" % provider)
    model = str(cfg.get("model") or "").strip() or _prov.get(provider).get("default_model", "")
    if not model:
        raise RuntimeError("未配置模型名（服务商 %s）" % provider)

    url = _prov.chat_url(provider, base, cfg.get("api_key") or "", model)
    headers = _prov.build_headers(provider, cfg.get("api_key") or "")
    payload = _prov.build_chat_body(
        provider, model, messages,
        temperature=float(cfg.get("temperature", 0.85) or 0.85),
        max_tokens=int(cfg.get("max_tokens") or 0),
        reasoning_effort=cfg.get("reasoning_effort") or None,
    )

    total = int(timeout or cfg.get("timeout") or 90)
    # ===== 审计：记录调用来源（2026-09-30 排查「无人操作却高频调用」时加的）=====
    _src = []
    try:
        import traceback as _tb, io as _io
        _sbuf = _io.StringIO()
        _tb.print_stack(file=_sbuf)
        _src = [l for l in _sbuf.getvalue().splitlines()
                if "ai.py" not in l and "_call" not in l and l.strip().startswith("File")][:3]
        print(f"[AI审计] provider={provider} model={model} msgs={len(messages)} "
              f"reason={cfg.get('reasoning_effort')} 调用来源:\n" + "\n".join(_src), flush=True)
    except Exception:
        pass
    # ===== 审计结束 =====

    reason = cfg.get("reasoning_effort")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=total)
        ) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                body = await r.text()
                if r.status not in (200, 201):
                    raise RuntimeError(
                        "AI 请求失败 HTTP %s: %s" % (r.status, _short(body))
                    )
        try:
            data = json.loads(body)
        except Exception:
            raise RuntimeError("AI 返回的不是 JSON：%s" % _short(body))
        try:
            reply = _prov.parse_text_response(provider, data)
        except ValueError as e:
            raise RuntimeError("AI 响应解析失败：%s | %s" % (e, _short(body)))
        # 用量（各服务商字段不同，尽力解析，拿不到就 None）
        pt = ct = None
        try:
            u = data.get("usage") or {}
            pt = u.get("prompt_tokens") or u.get("input_tokens")
            ct = u.get("completion_tokens") or u.get("output_tokens")
        except Exception:
            pass
        _log_usage(provider, model, len(messages), reason, pt, ct, True, source="\n".join(_src))
        return reply
    except Exception as e:
        _log_usage(provider, model, len(messages), reason, None, None, False, str(e)[:200])
        raise


def _short(text, n=300):
    """错误信息里附带一小段响应体，方便定位（去掉换行避免刷屏）。"""
    return " ".join(str(text or "").split())[:n]


async def test_ping(msg="你好，在吗喵"):
    cfg = await get_config()
    msgs = [
        {"role": "system", "content": "你是连接测试助手。收到消息只需简单回复一句即可。"},
        {"role": "user", "content": msg},
    ]
    return await _call(cfg, msgs, timeout=30)


def list_providers():
    """供 WebUI 渲染服务商下拉与默认值。"""
    return _prov.list_for_ui()


async def fetch_models():
    """拉取当前服务商的模型列表，返回 [{id, name}]。

    Gemini / Anthropic 的响应结构与 OpenAI 不同，统一交给 providers.parse_models。
    """
    import aiohttp

    cfg = await get_config()
    provider = str(cfg.get("provider") or "other").lower()
    p = _prov.get(provider)
    if not p.get("has_models"):
        return []
    base = str(cfg.get("base_url") or "").strip() or p.get("default_base", "")
    if not base:
        return []
    url = _prov.models_url(provider, base, cfg.get("api_key") or "")
    headers = _prov.build_headers(provider, cfg.get("api_key") or "")
    headers.pop("Content-Type", None)  # GET 不需要
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(url, headers=headers) as r:
            body = await r.text()
            if r.status != 200:
                raise RuntimeError("获取模型列表失败 HTTP %s: %s" % (r.status, _short(body)))
    try:
        data = json.loads(body)
    except Exception:
        raise RuntimeError("模型列表不是 JSON：%s" % _short(body))
    return _prov.parse_models(provider, data)


async def fetch_balance():
    """尽力而为的余额查询；不同服务商端点不同，查不到返回 None。"""
    import aiohttp

    cfg = await get_config()
    provider = str(cfg.get("provider") or "other").lower()
    p = _prov.get(provider)
    path = p.get("balance_path")
    if not path:
        return None
    base = str(cfg.get("base_url") or "").strip() or p.get("default_base", "")
    if not base:
        return None
    url = _prov.build_url(provider, base, path, cfg.get("api_key") or "")
    headers = _prov.build_headers(provider, cfg.get("api_key") or "")
    headers.pop("Content-Type", None)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        return _prov.parse_balance(provider, data)
    except Exception:
        return None


# ---------- 对话 ----------
def _identity(ctx):
    """返回 (记忆钥匙 mem_key, 会话钥匙 sess_key, 昵称)。

    记忆钥匙直接用 openid（私聊=全局 user_openid，群聊=群内 member_openid）：
    不同人天然分开，同一个人一直用同一把钥匙，改昵称/换群昵称都不丢记忆。
    会话历史仍按「群+群内openid」隔离，避免不同群的当前话题互相串台。
    """
    author = getattr(getattr(ctx, "message", None), "author", None) or {}
    nickname = (author.get("username") if isinstance(author, dict) else getattr(author, "username", None)) or ""
    c2c_uoid = (author.get("user_openid") if isinstance(author, dict) else getattr(author, "user_openid", None)) or ""
    if getattr(ctx, "scene", None) == "c2c" and c2c_uoid:
        return "user:" + c2c_uoid, "user:" + c2c_uoid, nickname or "群友"
    goid = getattr(getattr(ctx, "message", None), "group_openid", None) or getattr(ctx, "target", None) or ""
    moid = getattr(ctx, "openid", "") or ""
    mem_key = ("op:" + moid) if moid else ("grp:" + goid)
    sess_key = "grp:%s:%s" % (goid, moid)
    return mem_key, sess_key, nickname or "群友"


async def chat_once(ctx, text):
    cfg = await get_config()
    mem_key, sess_key, nickname = _identity(ctx)

    sys_prompt = cfg["system_preset"] or ""
    mem = await get_memory(mem_key)
    if mem and (mem.get("memory") or mem.get("summary")):
        sys_prompt += (
            "\n\n【你对这个人已有的长期记忆】\n" + str(mem.get("memory") or "")
            + "\n【你对这个人的印象】\n" + str(mem.get("summary") or "")
        )

    history = _history.setdefault(sess_key, [])
    maxh = max(2, int(cfg.get("max_history", 12)))
    messages = [{"role": "system", "content": sys_prompt}]
    messages += history[-maxh * 2:]
    messages.append({"role": "user", "content": text})

    reply_text = await _call(cfg, messages)

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply_text})
    if len(history) > maxh * 2:
        del history[: len(history) - maxh * 2]

    # 后台触发记忆总结，不阻塞本次回复
    _schedule_summarize(cfg, mem_key, sess_key, nickname)
    return reply_text


# ---------- 记忆总结（AI 自总结，程序只保存） ----------
def _schedule_summarize(cfg, mem_key, sess_key, nickname):
    interval = int(cfg.get("memory_interval") or 0)
    if interval < 1:        # 0 = 关闭自动总结
        return
    history = _history.get(sess_key) or []
    if len(history) < interval * 2:
        return
    if mem_key in _summarizing:
        return
    _summarizing.add(mem_key)
    asyncio.get_running_loop().create_task(_do_summarize(cfg, mem_key, sess_key, nickname))


async def _do_summarize(cfg, mem_key, sess_key, nickname):
    try:
        history = _history.get(sess_key) or []
        prev = await get_memory(mem_key)
        chat_lines = "\n".join(f"{m['role']}: {m['content']}" for m in history[-12:])
        prompt = (
            "你是记忆整理器。根据下面这段你和某个群友的对话，用第一人称产出两小段中文总结：\n"
            "1) 记忆：你对这个人（主视角用户）的长期记忆——ta 是谁、喜好、身份、聊过什么、提到过的重要的事；\n"
            "2) 印象：你对这个人的简短印象/评价。\n"
            "每段 1~3 行。直接写成『记忆：』『印象：』开头的两段，不要输出别的。"
        )
        if prev.get("memory") or prev.get("summary"):
            prompt += ("\n\n【上一次的总结】\n记忆：" + str(prev.get("memory") or "")
                       + "\n印象：" + str(prev.get("summary") or ""))
        msg = [{"role": "system", "content": prompt}, {"role": "user", "content": "对话：\n" + chat_lines}]
        result = await _call(cfg, msg, timeout=40)
        memory = summary = ""
        for line in result.splitlines():
            if line.startswith("记忆"):
                memory = line.split("：", 1)[-1].strip()
            elif line.startswith("印象") or line.startswith("评价"):
                summary = line.split("：", 1)[-1].strip()
        if not memory and not summary:
            memory = result.strip()  # 兜底：整个结果当记忆
        await save_memory(mem_key, nickname, memory, summary)
        _log.info("已更新 %s 的记忆", mem_key)
    except Exception as e:
        _log.warning("记忆总结失败 %s: %s", mem_key, e)
    finally:
        _summarizing.discard(mem_key)


# ---------- 命令分发层兜底：是否接管这条消息 ----------
# 随机回复冷却（模块级，进程内共享）
_last_random_ts = 0.0


async def _random_reply_ok():
    """随机回复开关：是否对这条「未@的群消息」随机回一句。"""
    from config import (AI_RANDOM_REPLY_ENABLED, AI_RANDOM_REPLY_PROBABILITY,
                        AI_RANDOM_REPLY_COOLDOWN)
    global _last_random_ts
    if not AI_RANDOM_REPLY_ENABLED:
        return False
    now = time.time()
    if now - _last_random_ts < max(0, AI_RANDOM_REPLY_COOLDOWN):
        return False
    p = max(0.0, min(1.0, float(AI_RANDOM_REPLY_PROBABILITY or 0)))
    if p <= 0:
        return False
    if random.random() >= p:
        return False
    _last_random_ts = now
    return True


async def _send_peak_notice(ctx):
    """高峰时段被点名（@/私聊）时：发一张提醒图 + 随机一句话，替代 AI 回复。"""
    text = random.choice(PEAK_NOTICE_LINES)
    try:
        if os.path.isfile(PEAK_IMAGE):
            await ctx.sender.send_image_with_text(ctx.message, text, PEAK_IMAGE, reply=True)
        else:
            await ctx.reply(text)
    except Exception as e:
        _log.warning("高峰提醒发送失败: %s", e)
    return True


async def handle_candidate(ctx, text):
    '''由 dispatch 在末尾调用。返回 True 表示已接管（异步回复中）。'''
    if not await is_enabled():
        return False
    scene = getattr(ctx, "scene", None)
    text = (text or "").strip()
    if not text:
        return False

    # 高峰时段钱包保护：高峰时禁用 AI。被点名（@/私聊）时回一张提醒图；
    # 群聊随机触发在高峰时静默跳过（避免刷屏）。
    peak_blocked = False
    try:
        from bot.core import peak_guard
        peak_blocked = peak_guard.is_peak_blocked()
    except Exception:
        pass

    if scene == "group":
        # 群聊：@ 了机器人才稳定触发；未 @ 的消息按概率随机触发（增加趣味）
        at_me = getattr(getattr(ctx, "message", None), "at_me", False)
        if at_me:
            if peak_blocked:
                return await _send_peak_notice(ctx)
            asyncio.get_running_loop().create_task(_respond(ctx, text))
            return True
        if peak_blocked:
            return False
        if await _random_reply_ok():
            asyncio.get_running_loop().create_task(_respond(ctx, text))
            return True
        return False
    if scene == "c2c":
        if peak_blocked:
            return await _send_peak_notice(ctx)
        asyncio.get_running_loop().create_task(_respond(ctx, text))
        return True
    return False  # 其他未覆盖场景不处理


async def _respond(ctx, text):
    try:
        reply = await chat_once(ctx, text)
        if reply:
            await ctx.reply(reply)
    except Exception as e:
        _log.error("AI 响应失败: %s", e)
        try:
            await ctx.reply("呜，本喵脑袋卡顿了一下，稍后再试试喵。")
        except Exception:
            pass