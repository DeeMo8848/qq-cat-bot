# -*- coding: utf-8 -*-
"""「B站解析」命令：检测到 B站链接/BV号 时，用 BBDown 解析并发送封面、视频等。

触发方式：
    1. 消息里含 BV 号（如 BV1Fg411y79R 或 B站链接）-> 自动解析（封面 + 视频）
    2. 发送「B站解析」-> 显示使用教程
    3. 发送「下载视频/仅下载封面/仅下载视频/仅下载音频 (BV号或链接)」-> 只下载对应内容
    4. 隐藏功能：发送 BBDown 详细命令 -> 执行并发送下载的内容
"""

import os
import re
import shutil
import time

from . import register, ROLE_ALL
from bot.core import tools
from config import ROOT, BBDOWN_DIR, BBDOWN_EXE

# 下载临时目录（统一放 tmp/bili）
_TMP_ROOT = os.path.join(ROOT, "tmp", "bili")

# 用户明确要求「仅下载视频」时的上限（MB）
MAX_VIDEO_MB = 100
# 自动解析（检测到 BV 号）时的视频上限（MB）。QQ 视频超过 30MB 会降级成群文件无法直接点开看
AUTO_VIDEO_MB = 30

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

TUTORIAL = """【B站解析】使用教程
发送 B站链接 或 BV号，我会自动解析并发送封面和低画质视频的喵

关键词：
· 下载视频 (BV号或链接)  → 只下载P1喵（原画质）
· 仅下载封面 (BV号或链接)  → 只下载封面喵
· 仅下载视频 (BV号或链接)  → 只下载视频喵
· 仅下载音频 (BV号或链接)  → 只下载音频喵

注意：自动解析默认低画质（≤30MB），如需原画质请用「下载视频」喵"""


def extract_bv(text):
    m = BV_RE.search(text or "")
    return m.group(0) if m else None


def _matcher(text):
    # 含 BV 号/B站链接 -> 自动解析；整条消息精确为「B站解析」-> 显示教程。
    # 用精确匹配避免「· 📺B站解析」「我要B站解析」等被子串误触发。
    if extract_bv(text):
        return True
    return (text or "").strip().lower() == "b站解析"


@register(keywords=["B站解析", "b站解析"], help="发链接/BV号自动解析B站视频喵", matcher=_matcher, role=ROLE_ALL, exact=True)
async def cmd_bilibili(ctx):
    text = (ctx.args or "").strip()
    bv = extract_bv(text)

    # 隐藏功能：BBDown 详细命令
    if text.upper().startswith("BBDOWN"):
        await _run_bbdown_command(ctx, text)
        return

    # 发送「B站解析」-> 教程
    if not bv:
        await ctx.reply(TUTORIAL)
        return

    # 关键词操作（「下载视频」在前，同时覆盖「仅下载视频」）
    if "下载视频" in text:
        await _download_and_send(ctx, bv, "video")
    elif "仅下载封面" in text:
        await _download_and_send(ctx, bv, "cover")
    elif "仅下载音频" in text:
        await _download_and_send(ctx, bv, "audio")
    else:
        await _auto_parse(ctx, bv)


# 标记该命令受「被动解析」模式约束：passive 时群里未 @ 机器人则不在群里自动解析
cmd_bilibili.passive_gate = True


# ---------- 信息获取 ----------
async def _get_info(bv):
    """用 --only-show-info 获取视频信息，返回 dict 或 None。"""
    out, err, _ = await tools.run_script(f'"{BBDOWN_EXE}" {bv} --only-show-info', timeout=60)
    text = out + err
    info = {}
    m = re.search(r"视频标题:\s*(.+)", text)
    if m:
        info["title"] = m.group(1).strip()
    m = re.search(r"发布时间:\s*([\d\- :]+)", text)
    if m:
        info["publish_date"] = m.group(1).strip()
    m = re.search(r"UP主页:\s*(\S+)", text)
    if m:
        info["up_url"] = m.group(1).strip()
    # 预估合成后文件大小：最高画质视频流 + 首个音频流
    total = 0.0
    m = re.search(r"\[~([\d.]+)\s*MB\]", text)
    if m:
        total += float(m.group(1))
    audio_part = text.split("音频流", 1)
    if len(audio_part) > 1:
        m = re.search(r"\[~([\d.]+)\s*MB\]", audio_part[1])
        if m:
            total += float(m.group(1))
    if total:
        info["size_mb"] = total
    return info or None


# ---------- 下载 ----------
def _fresh_workdir():
    """创建本次下载的独立临时目录，返回路径。"""
    workdir = os.path.join(_TMP_ROOT, str(int(time.time() * 1000)))
    os.makedirs(workdir, exist_ok=True)
    return workdir


def _find_downloaded(workdir, exts):
    """在目录里找指定扩展名的文件，返回第一个匹配路径或 None。"""
    if not os.path.isdir(workdir):
        return None
    for name in os.listdir(workdir):
        full = os.path.join(workdir, name)
        if os.path.isfile(full) and name.lower().endswith(exts):
            return full
    return None


async def _download_cover(bv, workdir):
    await tools.run_script(f'"{BBDOWN_EXE}" {bv} --cover-only --work-dir "{workdir}"', timeout=120)
    return _find_downloaded(workdir, (".png", ".jpg", ".jpeg", ".webp"))


async def _download_video(bv, workdir, low_quality=False):
    if low_quality:
        # 自动解析默认低画质：480P 优先 HEVC/AV1，控制体积便于群里直接点开看
        await tools.run_script(
            f'"{BBDOWN_EXE}" {bv} -p 1 -q "480P 清晰, 360P 流畅" -e "hevc,av1,avc" --work-dir "{workdir}"',
            timeout=600,
        )
    else:
        await tools.run_script(f'"{BBDOWN_EXE}" {bv} -p 1 --work-dir "{workdir}"', timeout=600)
    return _find_downloaded(workdir, (".mp4", ".mkv", ".flv", ".mov"))


async def _download_audio(bv, workdir):
    await tools.run_script(f'"{BBDOWN_EXE}" {bv} --audio-only --work-dir "{workdir}"', timeout=600)
    return _find_downloaded(workdir, (".m4a", ".mp3", ".flac", ".wav", ".aac"))


# ---------- 自动解析（封面 + 视频） ----------
async def _auto_parse(ctx, bv):
    await ctx.reply_text("🐟️ 正在投喂猫猫，请稍候喵…")

    info = await _get_info(bv)
    if not info:
        await ctx.reply("解析失败，请检查 BV 号是否正确")
        return

    workdir = _fresh_workdir()
    try:
        # 1. 下载封面
        cover = await _download_cover(bv, workdir)
        if not cover:
            await ctx.reply("封面下载失败")
            return

        # 2. 发送封面消息：标题 + 封面图 + 发布时间（合并为一条图文混排消息）
        title = info.get("title", "未知标题")
        pub = info.get("publish_date", "")
        text = f"标题: {title}"
        if pub:
            text += f"\n{pub}"
        result = await ctx.sender.send_image_with_text(ctx.message, text, cover)
        if isinstance(result, str):
            await ctx.reply_text(result)

        # 3. 下载低画质视频（自动解析默认低画质，控制体积以便群里直接点开看）
        await ctx.reply_text("🐱吃饱了喵，正在生产猫屎咖啡…")
        video = await _download_video(bv, workdir, low_quality=True)
        if not video:
            await ctx.reply_text("视频下载失败")
            return

        actual_mb = os.path.getsize(video) / 1024 / 1024
        if actual_mb > AUTO_VIDEO_MB:
            await ctx.reply_text(f"吃撑了喵（视频实际 {actual_mb:.1f}MB，超过 {AUTO_VIDEO_MB:g}MB），睡大觉了喵")
            return

        result = await ctx.sender.send_local_file(ctx.message, 2, video)
        if isinstance(result, str):
            await ctx.reply_text(result)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------- 仅下载封面/视频/音频 ----------
async def _download_and_send(ctx, bv, kind):
    labels = {"cover": "封面", "video": "视频", "audio": "音频"}
    await ctx.reply_text("保证完成任务喵！")

    workdir = _fresh_workdir()
    try:
        if kind == "cover":
            path = await _download_cover(bv, workdir)
            ftype = 1
        elif kind == "video":
            path = await _download_video(bv, workdir)
            ftype = 2
        else:
            path = await _download_audio(bv, workdir)
            ftype = 4  # 音频以文件形式发送（MP3/M4A 音乐文件），而非语音

        if not path:
            await ctx.reply_text(f"{labels[kind]}下载失败")
            return

        actual_mb = os.path.getsize(path) / 1024 / 1024
        if actual_mb > MAX_VIDEO_MB:
            await ctx.reply_text(f"吃撑了喵（{labels[kind]}实际 {actual_mb:.1f}MB，超过 {MAX_VIDEO_MB:g}MB），睡大觉了喵")
            return

        result = await ctx.sender.send_local_file(ctx.message, ftype, path)
        if isinstance(result, str):
            await ctx.reply_text(result)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------- 隐藏功能：执行 BBDown 命令 ----------
async def _run_bbdown_command(ctx, text):
    """执行用户发送的 BBDown 详细命令，并发送下载的内容。"""
    workdir = _fresh_workdir()
    try:
        await ctx.reply_text("保证完成任务喵！")
        # 只允许 BBDown 开头的命令；把 BBDown 替换为完整路径。
        # 注意：替换串里含 Windows 反斜杠路径，必须用函数替换，否则 \B 等会被 re 当成非法转义
        cmd = re.sub(r"^BBDown\b", lambda m: f'"{BBDOWN_EXE}"', text, flags=re.IGNORECASE)
        if "--work-dir" not in cmd:
            cmd = f'{cmd} --work-dir "{workdir}"'
        await tools.run_script(cmd, timeout=600)

        # 发送下载的文件（图片/视频/音频）
        sent = False
        for exts, ftype in [
            ((".png", ".jpg", ".jpeg", ".webp"), 1),
            ((".mp4", ".mkv", ".flv", ".mov"), 2),
            ((".m4a", ".mp3", ".flac", ".wav", ".aac"), 4),  # 音频以文件形式发送
        ]:
            path = _find_downloaded(workdir, exts)
            if path:
                result = await ctx.sender.send_local_file(ctx.message, ftype, path)
                if isinstance(result, str):
                    await ctx.reply_text(result)
                sent = True
        if not sent:
            await ctx.reply_text("命令执行完成，但没有找到可发送的文件")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
