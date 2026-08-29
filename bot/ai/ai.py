# -*- coding: utf-8 -*-
"""AI 对话模块：接入任意 OpenAI 兼容 API（DeepSeek / 硅基流动 / OpenAI … 只需一个 API Key）。

设计要点：
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

from config import ROOT

_log = logging.getLogger("ai")

_CACHE_DIR = os.path.join(ROOT, "cache")
_CONFIG_FP = os.path.join(_CACHE_DIR, "ai_config.json")
_MEMORY_FP = os.path.join(_CACHE_DIR, "ai_memory.json")

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
}

_PROVIDER_BASE = {
    "deepseek": "https://api.deepseek.com",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "openai": "https://api.openai.com/v1",
    "other": "",
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
async def get_config():
    async with _lock:
        cfg = dict(_DEFAULTS)
        cfg.update({k: v for k, v in _read_json(_CONFIG_FP, {}).items() if k in _DEFAULTS})
        return cfg


async def save_config(data):
    async with _lock:
        cfg = dict(_DEFAULTS)
        cfg.update({k: v for k, v in _read_json(_CONFIG_FP, {}).items() if k in _DEFAULTS})
        for k in _DEFAULTS:
            if k in data and data[k] not in (None, ""):
                cfg[k] = data[k]
        _write_json(_CONFIG_FP, cfg)
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


async def save_memory(openid, nickname, memory, summary, relations="", portrait=""):
    async with _lock:
        data = _read_json(_MEMORY_FP, {})
        old = data.get(openid) or {}
        data[openid] = {
            "nickname": nickname or old.get("nickname", ""),
            "memory": memory or old.get("memory", ""),
            "summary": summary or old.get("summary", ""),
            "relations": relations or old.get("relations", ""),
            "portrait": portrait or old.get("portrait", ""),
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


# ---------- OpenAI 兼容调用 ----------
async def _call(cfg, messages, timeout=90):
    import aiohttp

    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    }
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": float(cfg.get("temperature", 0.85)),
        "stream": False,
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.post(url, json=payload, headers=headers) as r:
            if r.status not in (200, 201):
                body = await r.text()
                raise RuntimeError(f"AI 请求失败 HTTP {r.status}: {body[:200]}")
            data = await r.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError("AI 响应格式异常")


async def test_ping(msg="你好，在吗喵"):
    cfg = await get_config()
    msgs = [
        {"role": "system", "content": "你是连接测试助手。收到消息只需简单回复一句即可。"},
        {"role": "user", "content": msg},
    ]
    return await _call(cfg, msgs, timeout=30)


async def fetch_models():
    import aiohttp

    cfg = await get_config()
    if not cfg["base_url"]:
        return []
    url = str(cfg["base_url"]).rstrip("/") + "/models"
    headers = {"Authorization": "Bearer " + cfg["api_key"]}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get(url, headers=headers) as r:
            if r.status != 200:
                raise RuntimeError(f"获取模型列表失败 HTTP {r.status}")
            data = await r.json()
    return [m.get("id") for m in data.get("data", []) if m.get("id")]


async def fetch_balance():
    '''尽力而为的余额查询；不同服务商端点不同，查不到返回 None。'''
    import aiohttp

    cfg = await get_config()
    if not cfg["base_url"]:
        return None
    provider = str(cfg.get("provider", "")).lower()
    base = str(cfg["base_url"]).rstrip("/")
    headers = {"Authorization": "Bearer " + cfg["api_key"]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
            if provider == "deepseek":
                async with s.get(base + "/user/balance", headers=headers) as r:
                    if r.status != 200:
                        return None
                    infos = (await r.json()).get("balance_infos") or []
                    if not infos:
                        return None
                    return {"provider": "DeepSeek", "total": infos[0].get("total_balance"), "currency": infos[0].get("currency", "CNY")}
            if provider == "siliconflow":
                async with s.get(base + "/user/info", headers=headers) as r:
                    if r.status != 200:
                        return None
                    bal = (await r.json()).get("data", {}).get("balance")
                    if bal is None:
                        return None
                    return {"provider": "硅基流动", "total": bal, "currency": "CNY"}
            # OpenAI / 兼容：订阅端点（很可能无权限，查不到即不支持）
            async with s.get(base + "/dashboard/billing/subscription", headers=headers) as r:
                if r.status != 200:
                    return None
                return {"provider": "OpenAI", "total": (await r.json()).get("hard_limit_usd"), "currency": "USD"}
    except Exception:
        return None


# ---------- 对话 ----------
def _norm_name(n):
    """昵称归一化，用于跨群识别身份（同一昵称视为同一人）。"""
    n = (n or "").strip()
    return " ".join(n.split())


def _identity(ctx):
    """返回 (记忆钥匙 mem_key, 会话钥匙 sess_key, 昵称)。

    - 私聊：用全局唯一的 user_openid 当记忆钥匙 → 全站统一。
    - 群聊：拿不到跨群的全局账号 ID，改用昵称当记忆钥匙 → 同一个人换个群也能被认出；
      会话历史仍按「群+群内openid」隔离，避免不同群的当前话题互相串台。
    """
    author = getattr(getattr(ctx, "message", None), "author", None) or {}
    nickname = (author.get("username") if isinstance(author, dict) else getattr(author, "username", None)) or ""
    c2c_uoid = (author.get("user_openid") if isinstance(author, dict) else getattr(author, "user_openid", None)) or ""
    if getattr(ctx, "scene", None) == "c2c" and c2c_uoid:
        return "user:" + c2c_uoid, "user:" + c2c_uoid, nickname or "群友"
    goid = getattr(getattr(ctx, "message", None), "group_openid", None) or getattr(ctx, "target", None) or ""
    moid = getattr(ctx, "openid", "") or ""
    name = _norm_name(nickname)
    mem_key = ("name:" + name) if name else ("op:" + (moid or "anon"))
    sess_key = "grp:%s:%s" % (goid, moid)
    return mem_key, sess_key, nickname or "群友"


async def chat_once(ctx, text):
    cfg = await get_config()
    mem_key, sess_key, nickname = _identity(ctx)

    sys_prompt = cfg["system_preset"] or ""
    mem = await get_memory(mem_key)
    if mem and (mem.get("memory") or mem.get("summary") or mem.get("relations") or mem.get("portrait")):
        sys_prompt += (
            "\n\n【你对这个人已有的长期记忆】\n" + str(mem.get("memory") or "")
            + "\n【你对这个人的评价】\n" + str(mem.get("summary") or "")
            + "\n【你对这个人的画像】\n" + str(mem.get("portrait") or "")
        )
        if mem.get("relations"):
            sys_prompt += "\n【这个人和其他人的关系网】\n" + str(mem.get("relations"))

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
            "你是记忆整理器。根据下面这段你和某个/某些群友的对话，用第一人称产出四小段中文总结：\n"
            "1) 记忆：你对这个人（主视角用户）的长期记忆（ta 的喜好、身份、提到过的事、聊过的内容）；\n"
            "2) 评价：你对这个人的评价总结；\n"
            "3) 关系：在对话中出现的群友之间的关系网（谁和谁是朋友/情侣/家人/同事…，谁对谁什么态度），没有就写'暂无明显关系'。\n"
            "4) 画像：基于 ta 的发言，提炼性格标签（优点与缺点都要，覆盖核心性格维度，不刻意褒贬），"
            "并给一句简短相处建议。\n"
            "每段 1~3 行。直接写成『记忆：』『评价：』『关系：』『画像：』开头的四段。"
        )
        if prev.get("memory") or prev.get("summary") or prev.get("relations") or prev.get("portrait"):
            prompt += ("\n\n【上一次的总结】\n记忆：" + str(prev.get("memory") or "")
                       + "\n评价：" + str(prev.get("summary") or "")
                       + "\n关系：" + str(prev.get("relations") or "")
                       + "\n画像：" + str(prev.get("portrait") or ""))
        msg = [{"role": "system", "content": prompt}, {"role": "user", "content": "对话：\n" + chat_lines}]
        result = await _call(cfg, msg, timeout=40)
        memory = summary = relations = portrait = ""
        for line in result.splitlines():
            if line.startswith("记忆"):
                memory = line.split("：", 1)[-1].strip()
            elif line.startswith("评价"):
                summary = line.split("：", 1)[-1].strip()
            elif line.startswith("关系"):
                relations = line.split("：", 1)[-1].strip()
            elif line.startswith("画像"):
                portrait = line.split("：", 1)[-1].strip()
        if not memory and not summary and not relations and not portrait:
            memory = result.strip()  # 兜底：整个结果当记忆
        await save_memory(mem_key, nickname, memory, summary, relations, portrait)
        _log.info("已更新 %s 的记忆", mem_key)
    except Exception as e:
        _log.warning("记忆总结失败 %s: %s", mem_key, e)
    finally:
        _summarizing.discard(mem_key)


# ---------- 命令分发层兜底：是否接管这条消息 ----------
async def handle_candidate(ctx, text):
    '''由 dispatch 在末尾调用。返回 True 表示已接管（异步回复中）。'''
    if not await is_enabled():
        return False
    scene = getattr(ctx, "scene", None)
    text = (text or "").strip()
    if not text:
        return False
    if scene == "group":
        # 群聊只响应「@ 了机器人」的消息（缺失标记就保守不触发）
        at_me = getattr(getattr(ctx, "message", None), "at_me", False)
        if not at_me:
            return False
    elif scene != "c2c":
        return False  # 其他未覆盖场景不处理
    asyncio.get_running_loop().create_task(_respond(ctx, text))
    return True


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