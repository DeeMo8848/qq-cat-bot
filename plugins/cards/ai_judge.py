# -*- coding: utf-8 -*-
"""🃏 AI 品级判定：用 DeepSeek 视觉模型对卡面图像判稀有度（铜/银/金/虹），
文本型卡额外给出标题与描述。独立调用，不受 AI 对话 enabled 开关影响。

模型：cache/ai_config.json 的 vision_model 字段（默认 deepseek-flash，
DeepSeek 官方多模态模型，支持 Chat Completions 传图）；base_url/api_key 复用该配置。
"""

import aiohttp
import base64
import json
import logging
import os
import random

from config import ROOT

_log = logging.getLogger("cards.ai")

_CONFIG_FP = os.path.join(ROOT, "cache", "ai_config.json")
DEFAULT_VISION_MODEL = "deepseek-flash"

SYSTEM_PROMPT = (
    "你是「卡牌品级判定师」，只根据用户提供的卡面图像做客观判定，不闲聊、不解释。\n"
    "判定标准（严格按图像的内容质量与完成度，与题材、画风无关）：\n"
    "- 铜：普通日常照 / 随手拍 / 简单涂鸦 / 低清模糊 / 截图 / 完成度低的素材\n"
    "- 银：较精美 / 主体清晰 / 有明确主题和构图的插画或照片 / 完成度中等\n"
    "- 金：精美高质量 / 精致立绘 / 光影构图讲究 / 高完成度 / 适合收藏\n"
    "- 虹：顶级 / 大师级 / 绝美 / 神级 / 极具艺术与收藏价值的作品\n"
    "规则：\n"
    "1. 只输出一个 JSON 对象，禁止输出任何其他文字、解释或 Markdown 代码块围栏。\n"
    "2. JSON 结构固定为：{\"rarity\":\"铜|银|金|虹\",\"title\":\"\",\"desc\":\"\"}\n"
    "3. rarity 只能取「铜 / 银 / 金 / 虹」之一。\n"
    "4. 若本次为文本型卡牌（用户要求带标题和描述）：title 给一个不超过 8 字、贴合画面的卡牌标题；"
    "desc 给一句不超过 30 字、有游戏卡牌感的「效果描述」（可虚构，类似「登场时：使我方全体攻击力+2」）。"
    "若为非文本型卡牌，title 和 desc 都必须为空字符串 \"\"。\n"
    "5. 判定宁严勿松：普通质量的图片不要轻易给金 / 虹。"
)


def _cfg() -> dict:
    data = {}
    try:
        with open(_CONFIG_FP, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        pass
    return {
        "api_key": str(data.get("api_key") or "").strip(),
        "base_url": str(data.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        "model": str(data.get("vision_model") or DEFAULT_VISION_MODEL).strip(),
        "temperature": 0.2,
    }


def _parse(text: str) -> dict:
    """从模型输出里稳健提取 {rarity,title,desc}。"""
    t = (text or "").strip()
    t = t.strip("`").strip()
    if t.startswith("json"):
        t = t[4:].strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("无 JSON")
    obj = json.loads(t[start:end + 1])
    rarity = str(obj.get("rarity") or "").strip()
    if rarity not in ("铜", "银", "金", "虹"):
        raise ValueError(f"品级非法: {rarity}")
    return {
        "rarity": rarity,
        "title": str(obj.get("title") or "").strip()[:20],
        "desc": str(obj.get("desc") or "").strip()[:60],
    }


def _random_rarity() -> str:
    """加权随机品级兜底：铜(60%) > 银(25%) > 金(12%) > 虹(3%)。"""
    return random.choices(
        ["铜", "银", "金", "虹"], weights=[0.60, 0.25, 0.12, 0.03]
    )[0]


def random_fallback() -> dict:
    """直接返回随机品级兜底结果（跳过 AI 评估时使用）。"""
    return {"rarity": _random_rarity(), "title": "", "desc": ""}


async def judge_card(image_bytes: bytes, need_text: bool) -> dict:
    """判品级；任何失败都返回兜底结果（不抛异常），保证制作流程不中断。"""
    cfg = _cfg()
    if not cfg["api_key"]:
        _log.warning("未配置 AI API Key，品级兜底为随机")
        return random_fallback()
    note = ("本次为文本型卡牌，请按规则给出标题与效果描述。"
            if need_text else "本次为无文本型卡牌，仅判定品级。")
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "这是卡面图像。" + note},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ]
    url = cfg["base_url"] + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]}
    payload = {"model": cfg["model"], "messages": messages,
               "temperature": cfg["temperature"], "thinking": {"type": "disabled"},
               "stream": False}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status not in (200, 201):
                    body = (await r.text())[:200]
                    raise RuntimeError(f"AI 请求失败 HTTP {r.status}: {body}")
                data = await r.json()
        content = data["choices"][0]["message"]["content"]
        return _parse(content)
    except Exception as e:
        _log.warning("品级判定失败，兜底为随机：%s", e)
        return random_fallback()
