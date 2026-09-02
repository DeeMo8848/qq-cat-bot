# -*- coding: utf-8 -*-
"""幻影坦克图片生成。

移植自 astrbot 插件 astrbot_plugin_mirage_tank（v1.2.2，MIT）的核心算法
与两轮会话交互（先发表图、再发里图）。分黑白与彩色两种模式。

交互方式（每用户独立会话）：
  幻影坦克        -> 黑白幻影坦克
  彩色幻影坦克     -> 彩色幻影坦克
  首条：机器人提示发送「表图」
  次条：表图收到，提示发送「里图」
  第三条：里图收到，生成并发送成品
  任意步骤发「取消」可中止
源文件仅在 bot/commands/dispatch 顶层被 consume 拦截，不影响其它命令。
"""

import asyncio
import io
import os
import time
import uuid

import aiohttp
import numpy as np
from PIL import Image

from config import ROOT
from bot.commands import register, ROLE_ALL

# 供 Web 后台「其他功能 → 幻影坦克」插件总开关使用的命令名集合
MIRAGE_CMD_NAMES = {"cmd_mirage", "cmd_mirage_color"}

# ---------- 会话状态 ----------
# openid -> {mode, state, front_path, scene, target, expires}
SESSIONS = {}

_SESSION_TIMEOUT = 60
_MAX_IMG = 10 * 1024 * 1024  # 10MB，与原插件一致
_COLOR_PARAMS = dict(a=0.5, b=20, w=0.7)

_TMP = os.path.join(ROOT, "tmp", "mirage")
os.makedirs(_TMP, exist_ok=True)

_BROWSER_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}


def _cleanup(s):
    for key in ("front_path", "back_path", "result_path"):
        p = s.get(key)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


# ---------- 下载/保存 ----------
async def _download_png(url):
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as session:
        async with session.get(url, timeout=timeout, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"下载失败 HTTP {resp.status}")
            content = await resp.read()
    if len(content) > _MAX_IMG:
        raise RuntimeError("图片超过 10MB")
    path = os.path.join(_TMP, uuid.uuid4().hex + ".png")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save_sync, content, path)
    return path


def _save_sync(content, path):
    with Image.open(io.BytesIO(content)) as img:
        img.save(path, format="PNG")
    return path


# ---------- 算法 ----------
def _gray_tank_sync(front_path, back_path, out_path):
    with Image.open(front_path) as f_img, Image.open(back_path) as b_img:
        image_f = f_img.convert("L")
        image_b = b_img.convert("L")
        w, h = min(image_f.width, image_b.width), min(image_f.height, image_b.height)
        image_f = image_f.resize((w, h), Image.Resampling.LANCZOS)
        image_b = image_b.resize((w, h), Image.Resampling.LANCZOS)
        array_f = np.array(image_f, dtype=np.float64)
        array_b = np.array(image_b, dtype=np.float64)
        a = b = 5
        with np.errstate(divide="ignore", invalid="ignore"):
            wf = array_f * a / 10 + 128
            wb = array_b * b / 10
            alpha = 1.0 - wf / 255.0 + wb / 255.0
            r_new = np.where(np.abs(alpha) > 1e-6, wb / alpha, 255.0)
        px = np.zeros((h, w, 4), dtype=np.uint8)
        px[:, :, 0] = np.clip(r_new, 0, 255).astype(np.uint8)
        px[:, :, 1] = px[:, :, 0]
        px[:, :, 2] = px[:, :, 0]
        px[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(px, mode="RGBA").save(out_path, format="PNG")


def _color_tank_sync(front_path, back_path, out_path, a, b, w):
    with Image.open(front_path) as a_raw, Image.open(back_path) as b_raw:
        a_img = a_raw.convert("RGB")
        b_img = b_raw.convert("RGB")
        w_img, h_img = a_img.size
        b_img = b_img.resize((w_img, h_img), Image.LANCZOS)
        a_arr = np.array(a_img, dtype=np.float32)
        b_arr = np.array(b_img, dtype=np.float32)
    a_gray = 0.299 * a_arr[:, :, 0] + 0.587 * a_arr[:, :, 1] + 0.114 * a_arr[:, :, 2]
    b_gray = 0.299 * b_arr[:, :, 0] + 0.587 * b_arr[:, :, 1] + 0.114 * b_arr[:, :, 2]
    b_gray = a * b_gray + b
    alpha = 255.0 - a_gray + b_gray
    alpha = np.clip(alpha, 1, 255).astype(np.uint8)
    alpha_3d = alpha.reshape(h_img, w_img, 1)
    alpha_f = alpha_3d.astype(np.float32)
    base = (1.0 - w) * a_arr + w * b_arr
    p = (base - (255.0 - alpha_f)) / (alpha_f / 255.0)
    p = np.clip(p, 0, 255).astype(np.uint8)
    rgba = np.concatenate([p, alpha_3d], axis=2)
    Image.fromarray(rgba, mode="RGBA").save(out_path, format="PNG")


async def _generate(front_path, back_path, mode, out_path):
    loop = asyncio.get_running_loop()
    if mode == "color":
        p = _COLOR_PARAMS
        await loop.run_in_executor(
            None, _color_tank_sync, front_path, back_path, out_path,
            p["a"], p["b"], p["w"])
    else:
        await loop.run_in_executor(
            None, _gray_tank_sync, front_path, back_path, out_path)


# ---------- 命令入口 ----------
async def _start(ctx, mode):
    openid = ctx.openid
    if openid in SESSIONS:
        await ctx.reply("你已有一个进行中的幻影坦克喵，先发「取消」结束它~")
        return
    SESSIONS[openid] = {
        "mode": mode,
        "state": "waiting_front",
        "front_path": None,
        "scene": ctx.scene,
        "target": ctx.target,
        "expires": time.time() + _SESSION_TIMEOUT,
    }
    await ctx.reply(f"请发送表图喵，{_SESSION_TIMEOUT}s 内有效（发「取消」可中止）")


@register(keywords=["幻影坦克"], help="生成黑白幻影坦克喵", exact=True, role=ROLE_ALL)
async def cmd_mirage(ctx):
    await _start(ctx, "gray")


@register(keywords=["彩色幻影坦克"], help="生成彩色幻影坦克喵", exact=True, role=ROLE_ALL)
async def cmd_mirage_color(ctx):
    await _start(ctx, "color")


# ---------- 多轮会话步进（由 dispatch 顶部拦截调用）----------
async def consume(ctx):
    """处理用户等待中的幻影坦克会话。返回 True 表示该消息已被本会话消费。"""
    s = SESSIONS.get(ctx.openid)
    if not s:
        return False
    # 会话必须在同一会话/目标里继续（不在别的群劫持）
    if s["scene"] != ctx.scene or s["target"] != ctx.target:
        return False
    # 超时自动丢弃
    if time.time() > s["expires"]:
        _cleanup(s)
        SESSIONS.pop(ctx.openid, None)
        return False

    text = (getattr(ctx.message, "content", None) or "").strip()
    if text == "取消":
        _cleanup(s)
        SESSIONS.pop(ctx.openid, None)
        await ctx.reply("已取消幻影坦克生成喵~")
        return True

    urls = getattr(ctx.message, "image_urls", None) or []
    if not urls:
        await ctx.reply("这不是一张图片，请重新发送喵~")
        return True

    try:
        img_path = await _download_png(urls[0])
    except Exception:
        _cleanup(s)
        SESSIONS.pop(ctx.openid, None)
        await ctx.reply("图片下载失败，请稍后再试喵~")
        return True

    s["expires"] = time.time() + _SESSION_TIMEOUT
    if s["state"] == "waiting_front":
        s["front_path"] = img_path
        s["state"] = "waiting_back"
        await ctx.reply("收到表图喵！请发送里图～")
        return True

    # waiting_back：生成
    s["back_path"] = img_path
    s["state"] = "processing"
    await ctx.reply("收到里图喵！请等待幻影坦克生成喵～")
    out_path = os.path.join(_TMP, uuid.uuid4().hex + ".png")
    try:
        await _generate(s["front_path"], img_path, s["mode"], out_path)
    except Exception:
        _cleanup(s)
        SESSIONS.pop(ctx.openid, None)
        await ctx.reply("图片处理失败，请稍后再试喵~")
        return True
    try:
        await ctx.sender.send_local_file(ctx.message, 1, out_path, reply=False)
        await ctx.reply("幻影坦克生成完毕！请签收喵~")
    except Exception:
        await ctx.reply("幻影坦克图片发送失败喵~")
    _cleanup(s)
    SESSIONS.pop(ctx.openid, None)
    return True