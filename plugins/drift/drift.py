# -*- coding: utf-8 -*-
"""漂流瓶。

移植自 astrbot 插件 astrbot_plugin_drift_bottle（协议见仓库 README）。
对接其使用的第三方漂流瓶服务（api.xiaoyunsha.cn）：
  · 捡瓶子    -> 随机捞一个别人的漂流瓶（文字+图片）
  · 投瓶子    -> 投递瓶子（文字+图片）；不带内容时进入交互模式，等你下一条消息
  · 瓶子统计  -> 查看自己投递的数据
  · 瓶子帮助  -> 使用说明

官方 QQ 平台没有 OneBot 的 get_stranger_info，原作者名解析做不了，改为把
对方 ID 截断打码显示；API Key 需在 settings.json 配 DRIFT_BOTTLE_API_KEY
（第三方服务要求，捡瓶不需要、投瓶必需）。
"""

import asyncio
import base64
import io
import json
import os
import re
import time
import uuid

import aiohttp

from config import ROOT
from bot.commands import register, ROLE_ALL

_API_BASE = "https://api.xiaoyunsha.cn/api/a/index.php"
_API_KEY = os.environ.get("DRIFT_BOTTLE_API_KEY", "")

_TMP = os.path.join(ROOT, "tmp", "drift")
os.makedirs(_TMP, exist_ok=True)


def _api_key() -> str:
    try:
        from config import _cfg
        return _cfg("DRIFT_BOTTLE_API_KEY", "")
    except Exception:
        return _API_KEY


# ---------- 交互模式会话 ----------
# openid -> {scene, target, expires}
SESSIONS = {}
_SESSION_TIMEOUT = 120

_BROWSER_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}


def _sid(openid: str) -> str:
    """把 openid 清洗成 API 要求的 1-50 位英文数字。"""
    cleaned = re.sub(r"[^0-9A-Za-z]", "", openid or "")[:50]
    return cleaned or "anonymous"


def _mask_id(uid: str) -> str:
    uid = str(uid or "")
    if len(uid) <= 5:
        return "神秘人" + uid
    return "QQ" + uid[:4] + "***"


async def _http(method: str, params: dict | None = None, timeout: float = 30):
    """封装 GET/POST，返回 dict；失败返回 {"code": -999, ...}。"""
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.request(method, _API_BASE, params=None if method.lower() == "post" else params,
                                 data=params if method.lower() == "post" else None,
                                 timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as r:
                return await r.json(content_type=None)
    except asyncio.TimeoutError:
        return {"code": -999, "msg": "请求超时喵"}
    except aiohttp.ClientError as e:
        return {"code": -999, "msg": f"网络请求失败: {e}"}
    except Exception:
        return {"code": -999, "msg": "请求异常喵"}


async def _download_bytes(url: str):
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30), ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


def _strip_cmd(text: str) -> str:
    out = re.sub(r"^(/?\s*(投瓶子|投|send|捡瓶子|捡|pick|瓶子统计|数据|瓶子帮助))\s*", "", text.strip(), flags=re.I).strip()
    return re.sub(r"\[图片\]|\[文件\]|\[表情\]|\[语音\]|\[视频\]", "", out).strip()


# ---------- 匹配器 ----------
def _pick_matcher(t):
    return (t or "").strip() in ("捡瓶子", "捡", "pick")


def _send_matcher(t):
    t = (t or "").strip()
    return (t in ("投瓶子", "投", "send")
            or t.startswith("投瓶子 ") or t.startswith("投 ")
            or t.startswith("send "))


def _stats_matcher(t):
    return (t or "").strip() in ("瓶子统计", "数据")


def _help_matcher(t):
    return (t or "").strip() == "瓶子帮助"


# ---------- 核心投递 ----------
async def _do_send(ctx, text: str, image_data: list):
    key = _api_key()
    if not key:
        await ctx.reply("❌ 投瓶子需要 API Key，请先在 settings.json 配置 DRIFT_BOTTLE_API_KEY")
        return
    params = {"id": _sid(ctx.openid), "key": key}
    if text:
        params["character"] = text
    if image_data:
        params["url"] = "base64://" + image_data
    result = await _http("POST" if image_data else "GET", params)
    code = result.get("code")
    if code == 0:
        data = result.get("data", {}) or {}
        await ctx.reply(f"✅ {data.get('message', '投递成功')}")
    else:
        err_map = {
            -3: "❌ 用户ID不能为空", -4: "❌ 内容或图片至少填写一个",
            -5: "❌ 图片URL格式不正确", -6: "❌ 投递失败，请稍后再试",
            -9: "❌ 图片下载失败，可能服务器无法访问该图",
            -10: "❌ 服务器图片目录不可写",
            -11: "❌ 用户ID格式不正确",
            -12: "❌ API Key 无效或已禁用，请检查 settings.json 的 DRIFT_BOTTLE_API_KEY",
        }
        await ctx.reply(err_map.get(code, f"❌ 投递失败: {result.get('msg', '未知错误')} (code={code})"))


# ---------- 命令 ----------
@register(keywords=["捡瓶子"], help="随机捞一个漂流瓶喵", matcher=_pick_matcher, role=ROLE_ALL)
async def cmd_drift_pick(ctx):
    result = await _http("GET")
    code = result.get("code")
    if code != 0:
        err_map = {
            -7: "🌊 海面一片平静，暂时没有漂流瓶喵~",
            -8: "❌ 捡瓶子失败，请稍后再试",
            -1: "⚠️ 漂流瓶系统尚未初始化",
            -2: "⚠️ 系统数据库连接异常",
        }
        await ctx.reply(err_map.get(code, f"❌ 捡瓶子失败: {result.get('msg')} (code={code})"))
        return
    data = result.get("data", {}) or {}
    bottle_id = data.get("bottle_id", "?")
    author = _mask_id(data.get("user_id", ""))
    content = (data.get("content") or "").strip()
    create_time = data.get("create_time", "未知时间")
    image_url = data.get("image_url", "")

    lines = [
        f"🍾 捡到一个漂流瓶！ (No.{bottle_id})",
        "━━━━━━━━━━━━━━━━",
        f"👤 来自: {author}",
        f"🕐 时间: {create_time}",
    ]
    if content:
        lines += ["━━━━━━━━━━━━━━━━", f"📝 内容:\n{content}"]
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("💡 发「投瓶子」写下你的故事，让下一个人捡到它！")
    await ctx.reply_text("\n".join(lines), reply=False)

    # 附带图片：下载后发送
    if image_url:
        data_bytes = await _download_bytes(image_url)
        if data_bytes:
            import time as _t
            p = os.path.join(_TMP, uuid.uuid4().hex + ".png")
            try:
                with open(p, "wb") as f:
                    f.write(data_bytes)
                await ctx.sender.send_local_file(ctx.message, 1, p, reply=False)
            except Exception:
                pass
            finally:
                try:
                    os.remove(p)
                except Exception:
                    pass


@register(keywords=["投瓶子"], help="投递一个漂流瓶（可带文字/图片）喵", matcher=_send_matcher, role=ROLE_ALL)
async def cmd_drift_send(ctx):
    raw = (getattr(ctx.message, "content", None) or "").strip()
    text = _strip_cmd(raw)
    image_urls = getattr(ctx.message, "image_urls", None) or []
    image_data = None
    if image_urls:
        data_bytes = await _download_bytes(image_urls[0])
        if data_bytes:
            image_data = base64.b64encode(data_bytes).decode("ascii")

    if not text and not image_data:
        # 交互模式：等待用户下一条消息
        SESSIONS[ctx.openid] = {
            "scene": ctx.scene,
            "target": ctx.target,
            "expires": time.time() + _SESSION_TIMEOUT,
        }
        await ctx.reply("🍾 要在瓶子上写什么呢？发文字、图片都可以~")
        return
    await _do_send(ctx, text, image_data)


@register(keywords=["瓶子统计"], help="查看自己的漂流瓶统计喵", matcher=_stats_matcher, role=ROLE_ALL)
async def cmd_drift_stats(ctx):
    result = await _http("GET", {"id": _sid(ctx.openid)})
    code = result.get("code")
    if code != 0:
        err_map = {
            -3: "❌ 用户ID无效", -1: "⚠️ 漂流瓶系统尚未初始化",
            -2: "⚠️ 系统数据库连接异常",
        }
        await ctx.reply(err_map.get(code, f"❌ 查询失败: {result.get('msg')} (code={code})"))
        return
    data = result.get("data", {}) or {}
    total = data.get("total", 0)
    approved = data.get("approved", 0)
    pending = data.get("pending", 0)
    rejected = data.get("rejected", 0)
    resp = [
        f"📊 漂流瓶统计（{_mask_id(ctx.openid)}）",
        "━━━━━━━━━━━━━━━━",
        f"📮 总投递数: {total}",
        f"✅ 已通过:   {approved}",
        f"⏳ 审核中:   {pending}",
        f"❌ 已拒绝:   {rejected}",
    ]
    if total > 0:
        resp.append(f"📈 通过率: {approved / total * 100:.1f}%")
    resp.append("💡 发「投瓶子」写下你的心情吧！")
    await ctx.reply_text("\n".join(resp), reply=False)


@register(keywords=["瓶子帮助"], help="漂流瓶使用说明喵", matcher=_help_matcher, role=ROLE_ALL)
async def cmd_drift_help(ctx):
    await ctx.reply_text(
        "🍾 漂流瓶 使用指南\n"
        "━━━━━━━━━━━━━━━━\n"
        "🔹 捡瓶子\n"
        "   随机捞取一个漂流瓶，看陌生人留言\n"
        "🔹 投瓶子 [文字内容]\n"
        "   投递漂流瓶，可带图片；不带内容则等你补发\n"
        "🔹 瓶子统计\n"
        "   查看投递数据\n"
        "🔹 瓶子帮助\n"
        "   显示本帮助",
        reply=False,
    )


# ---------- 交互模式步进（dispatch 顶部调用）----------
async def consume(ctx):
    s = SESSIONS.get(ctx.openid)
    if not s:
        return False
    if s["scene"] != ctx.scene or s["target"] != ctx.target:
        return False
    if time.time() > s["expires"]:
        SESSIONS.pop(ctx.openid, None)
        return False

    raw = (getattr(ctx.message, "content", None) or "").strip()
    # 用户发的是瓶子相关指令，不当作内容吞掉
    lower = raw.lower()
    if (raw and (raw.startswith(("捡", "投", "数据", "瓶子"))
                 or raw.startswith("/")) or lower.startswith("pick") or lower.startswith("send")):
        return False

    if not raw and not getattr(ctx.message, "image_urls", None):
        await ctx.reply("🍾 空瓶子投不出去哦，再说点什么吧~")
        return True

    SESSIONS.pop(ctx.openid, None)
    image_urls = getattr(ctx.message, "image_urls", None) or []
    image_data = None
    if image_urls:
        data_bytes = await _download_bytes(image_urls[0])
        if data_bytes:
            image_data = base64.b64encode(data_bytes).decode("ascii")
    await _do_send(ctx, _strip_cmd(raw), image_data)
    return True


# ---------- web 后台「其他功能」模块分组 ----------
# 功能组：一整个漂流瓶作为一个可开关的整体，不逐条列命令
DRIFT_CMD_NAMES = {"cmd_drift_pick", "cmd_drift_send", "cmd_drift_stats", "cmd_drift_help"}
OTHER_GROUPS = [
    ("drift_bottle", "漂流瓶", DRIFT_CMD_NAMES),
]
OTHER_CMD_NAMES = {n for _, _, ns in OTHER_GROUPS for n in ns}