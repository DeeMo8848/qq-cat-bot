# -*- coding: utf-8 -*-
"""AI 服务商表与请求构造（多服务商抽象层）。

背景：
    原来 bot 的 AI 接入只支持 4 家（deepseek / siliconflow / openai / other），
    而且 `_call()` 把「OpenAI 兼容」写死（`base_url + "/chat/completions"` +
    `Authorization: Bearer`）。想接 Gemini / Claude 就只能填「other」+ 手写完整
    路径，且必然失败（认证方式与请求体都不同）。

做法：
    参考同目录项目《AI密钥测试器》的 provider 表，把「服务商元数据」与
    「请求构造/响应解析」抽出来，让 ai.py 只关心对话逻辑。

关键概念：
    auth_type   认证方式    bearer（Authorization: Bearer x）
                          x-api-key（Anthropic 风格，需附带额外头）
                          query（Gemini 风格，key 拼在 URL 上）
    chat_format 请求/响应体格式
                          openai    /chat/completions + choices[0].message.content
                          gemini    :generateContent + candidates[0].content.parts[].text
                          anthropic /messages + content[].text

所有函数都是纯函数（不依赖 aiohttp / 网络），便于单测。
"""

from urllib.parse import quote as _quote

# ---------------------------------------------------------------- 服务商表
# 字段说明：
#   name/site/color      展示用
#   default_base         默认 API 根地址（不含路径）
#   models_path          模型列表路径（需鉴权）
#   chat_path            对话路径，可含 {model} 占位（Gemini 用）
#   auth_type            见模块 docstring
#   chat_format          见模块 docstring
#   default_model        默认模型
#   has_models           是否支持拉取模型列表
#   extra_headers        auth_type=x-api-key 时附加的头（如 anthropic-version）
#   capabilities         能力标签（仅展示用）
#   balance_path         余额查询路径（可选，无则不支持查余额）
#   balance_parse        余额解析规则（见 fetch_balance）
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "site": "platform.deepseek.com",
        "color": "#4D6BFE",
        "default_base": "https://api.deepseek.com",
        "models_path": "/models",
        "chat_path": "/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "deepseek-chat",
        "has_models": True,
        "capabilities": ["models", "chat", "vision", "balance"],
        "balance_path": "/user/balance",
        "balance_parse": "deepseek",
    },
    "siliconflow": {
        "name": "硅基流动",
        "site": "siliconflow.cn",
        "color": "#6155F5",
        "default_base": "https://api.siliconflow.cn",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "has_models": True,
        "capabilities": ["models", "chat", "vision", "balance"],
        "balance_path": "/v1/user/info",
        "balance_parse": "siliconflow",
    },
    "openai": {
        "name": "OpenAI",
        "site": "platform.openai.com",
        "color": "#10A37F",
        "default_base": "https://api.openai.com",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "gpt-4o-mini",
        "has_models": True,
        "capabilities": ["models", "chat", "vision"],
    },
    "gemini": {
        "name": "Google Gemini",
        "site": "ai.google.dev",
        "color": "#4285F4",
        "default_base": "https://generativelanguage.googleapis.com",
        "models_path": "/v1beta/models",
        "chat_path": "/v1beta/models/{model}:generateContent",
        "auth_type": "query",
        "chat_format": "gemini",
        "default_model": "gemini-2.5-flash",
        "has_models": True,
        "capabilities": ["models", "chat", "vision"],
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "site": "console.anthropic.com",
        "color": "#D97757",
        "default_base": "https://api.anthropic.com",
        "models_path": "/v1/models",
        "chat_path": "/v1/messages",
        "auth_type": "x-api-key",
        "chat_format": "anthropic",
        "default_model": "claude-3-5-haiku-20241022",
        "has_models": True,
        "extra_headers": {"anthropic-version": "2023-06-01"},
        "capabilities": ["models", "chat", "vision"],
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "site": "platform.moonshot.cn",
        "color": "#6B6B6B",
        "default_base": "https://api.moonshot.cn",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "moonshot-v1-8k",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "site": "open.bigmodel.cn",
        "color": "#3B5BFF",
        "default_base": "https://open.bigmodel.cn",
        "models_path": "/api/paas/v4/models",
        "chat_path": "/api/paas/v4/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "glm-4-flash",
        "has_models": True,
        "capabilities": ["models", "chat", "vision"],
    },
    "groq": {
        "name": "Groq",
        "site": "console.groq.com",
        "color": "#F55036",
        "default_base": "https://api.groq.com/openai",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "llama-3.3-70b-versatile",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "mistral": {
        "name": "Mistral AI",
        "site": "console.mistral.ai",
        "color": "#FF7000",
        "default_base": "https://api.mistral.ai",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "mistral-small-latest",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "together": {
        "name": "Together AI",
        "site": "api.together.xyz",
        "color": "#3D3D3D",
        "default_base": "https://api.together.xyz",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "meta-llama/Llama-3-8B-Chat-Turbo",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "xai": {
        "name": "xAI (Grok)",
        "site": "x.ai",
        "color": "#1A1A1A",
        "default_base": "https://api.x.ai",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "grok-beta",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "site": "openrouter.ai",
        "color": "#6C4CE1",
        "default_base": "https://openrouter.ai",
        "models_path": "/api/v1/models",
        "chat_path": "/api/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "google/gemini-flash-1.5",
        "has_models": True,
        "capabilities": ["models", "chat"],
    },
    "dashscope": {
        "name": "阿里通义千问",
        "site": "dashscope.aliyun.com",
        "color": "#615CED",
        "default_base": "https://dashscope.aliyuncs.com",
        "models_path": "/compatible-mode/v1/models",
        "chat_path": "/compatible-mode/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "qwen-turbo",
        "has_models": True,
        "capabilities": ["models", "chat", "vision"],
    },
    # 兜底：完全自定义（OpenAI 兼容），base_url 必须自己填完整根地址
    "other": {
        "name": "自定义 (OpenAI 兼容)",
        "site": "",
        "color": "#8A8A8A",
        "default_base": "",
        "models_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
        "auth_type": "bearer",
        "chat_format": "openai",
        "default_model": "",
        "has_models": True,
        "capabilities": ["chat"],
    },
}

# 可选服务商 id 列表（WebUI 下拉用）
PROVIDER_IDS = list(PROVIDERS.keys())


def get(provider_id: str) -> dict:
    """取某服务商元数据；未知 id 返回 other。"""
    return PROVIDERS.get((provider_id or "").strip().lower()) or PROVIDERS["other"]


def display_name(provider_id: str) -> str:
    return get(provider_id)["name"]


def list_for_ui() -> list:
    """给 WebUI 用的精简列表：[{id, name, site, default_base, default_model,
    auth_type, chat_format, has_models, capabilities}]。"""
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "id": pid,
            "name": p["name"],
            "site": p.get("site", ""),
            "default_base": p.get("default_base", ""),
            "default_model": p.get("default_model", ""),
            "auth_type": p.get("auth_type", "bearer"),
            "chat_format": p.get("chat_format", "openai"),
            "has_models": bool(p.get("has_models")),
            "capabilities": list(p.get("capabilities") or []),
        })
    return out


# ---------------------------------------------------------------- URL
def build_url(provider_id: str, base: str, path: str, api_key: str = "",
              model: str = "") -> str:
    """拼出完整 URL。

    - `{model}` 占位会被 URL 编码后替换（Gemini 的路径里带模型名）
    - auth_type=query 时把 key 作为查询参数附上
    """
    p = get(provider_id)
    url = (base or p.get("default_base") or "").rstrip("/") + path
    if model and "{model}" in url:
        url = url.replace("{model}", _quote(str(model), safe=""))
    if p.get("auth_type") == "query" and api_key:
        url += ("&" if "?" in url else "?") + "key=" + _quote(str(api_key), safe="")
    return url


def chat_url(provider_id: str, base: str, api_key: str, model: str) -> str:
    return build_url(provider_id, base, get(provider_id)["chat_path"], api_key, model)


def models_url(provider_id: str, base: str, api_key: str = "") -> str:
    return build_url(provider_id, base, get(provider_id)["models_path"], api_key)


# ---------------------------------------------------------------- 请求头
def build_headers(provider_id: str, api_key: str) -> dict:
    """按 auth_type 组装请求头。"""
    p = get(provider_id)
    headers = {"Content-Type": "application/json"}
    auth = p.get("auth_type", "bearer")
    if auth == "bearer":
        headers["Authorization"] = "Bearer " + str(api_key)
    elif auth == "x-api-key":
        headers["x-api-key"] = str(api_key)
    # query 认证不加头
    for k, v in (p.get("extra_headers") or {}).items():
        headers[k] = v
    return headers


# ---------------------------------------------------------------- 请求体
def _split_system(messages):
    """把 OpenAI 风格的 messages 拆成 (system 文本, 其余消息)。"""
    system = ""
    rest = []
    for m in messages or []:
        if m.get("role") == "system":
            system = (system + "\n\n" + (m.get("content") or "")).strip() if system \
                else (m.get("content") or "")
        else:
            rest.append(m)
    return system, rest


def build_chat_body(provider_id: str, model: str, messages: list,
                    temperature: float = 0.85, max_tokens: int = 0) -> dict:
    """按 chat_format 组装请求体（dict，交给 aiohttp 的 json= 参数）。"""
    fmt = get(provider_id).get("chat_format", "openai")
    system, rest = _split_system(messages)

    if fmt == "gemini":
        # Gemini：system 走 systemInstruction，对话走 contents，role 用 user/model
        contents = []
        for m in rest:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content") or ""}]})
        body = {
            "contents": contents,
            "generationConfig": {"temperature": float(temperature)},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = int(max_tokens)
        return body

    if fmt == "anthropic":
        # Anthropic：system 是顶层参数，messages 只允许 user/assistant，
        # 且 max_tokens 必填
        body = {
            "model": model,
            "messages": [{"role": m.get("role"), "content": m.get("content") or ""}
                         for m in rest],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens) if max_tokens else 2048,
        }
        if system:
            body["system"] = system
        return body

    # openai（默认）
    body = {
        "model": model,
        "messages": [{"role": m.get("role"), "content": m.get("content") or ""}
                     for m in (messages or [])],
        "temperature": float(temperature),
        "stream": False,
    }
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    return body


def parse_text_response(provider_id: str, data) -> str:
    """从响应里提取回复文本。取不到抛 ValueError（由上层转成可读错误）。"""
    if not isinstance(data, dict):
        raise ValueError("响应不是 JSON 对象")
    fmt = get(provider_id).get("chat_format", "openai")

    if fmt == "gemini":
        cands = data.get("candidates") or []
        if cands:
            parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            if text.strip():
                return text.strip()
        # 被安全策略拦截时 feedback 里会有原因
        fb = data.get("promptFeedback") or {}
        reason = fb.get("blockReason")
        raise ValueError("Gemini 未返回内容%s" % ("（%s）" % reason if reason else ""))

    if fmt == "anthropic":
        content = data.get("content") or []
        text = "".join(c.get("text", "") for c in content
                       if isinstance(c, dict) and c.get("type") == "text")
        if text.strip():
            return text.strip()
        if data.get("stop_reason") == "max_tokens":
            raise ValueError("Anthropic 回复被 max_tokens 截断（可调大 max_tokens）")
        raise ValueError("Anthropic 未返回文本")

    # openai
    choices = data.get("choices") or []
    if choices:
        c0 = choices[0] or {}
        msg = c0.get("message") or {}
        text = msg.get("content")
        if text is None:
            text = c0.get("text")  # 少数兼容实现用 text 字段
        if text:
            return str(text).strip()
        # 常见于「思考模式吃光输出预算」
        finish = c0.get("finish_reason")
        if finish == "length":
            raise ValueError("回复被长度限制截断（模型可能开了思考模式）")
    raise ValueError("响应里没有可用的回复内容")


# ---------------------------------------------------------------- 模型列表
def parse_models(provider_id: str, data) -> list:
    """解析模型列表，统一返回 [{id, name}]。"""
    if not isinstance(data, dict):
        return []
    out = []
    fmt_pid = (provider_id or "").lower()
    if fmt_pid == "gemini":
        for m in (data.get("models") or []):
            if not isinstance(m, dict):
                continue
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                out.append({"id": name, "name": m.get("displayName") or name})
        return out
    if fmt_pid == "anthropic":
        for m in (data.get("data") or []):
            if isinstance(m, dict) and m.get("id"):
                out.append({"id": m["id"], "name": m.get("display_name") or m["id"]})
        return out
    for m in (data.get("data") or []):
        if isinstance(m, dict) and m.get("id"):
            out.append({"id": m["id"], "name": m.get("name") or m["id"]})
    return out


# ---------------------------------------------------------------- 余额
def parse_balance(provider_id: str, data):
    """解析余额（仅 $ 支持的服务商）。查不到返回 None。"""
    p = get(provider_id)
    rule = p.get("balance_parse")
    if not rule or not isinstance(data, dict):
        return None
    try:
        if rule == "deepseek":
            infos = data.get("balance_infos") or []
            if infos:
                return {"provider": p["name"],
                        "total": infos[0].get("total_balance"),
                        "currency": infos[0].get("currency", "CNY")}
        elif rule == "siliconflow":
            bal = (data.get("data") or {}).get("balance")
            if bal is not None:
                return {"provider": p["name"], "total": bal, "currency": "CNY"}
    except Exception:
        return None
    return None
