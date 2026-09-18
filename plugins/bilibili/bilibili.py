# -*- coding: utf-8 -*-
"""「B站解析」命令：检测到 B站链接/BV号 时，用 BBDown 解析并发送封面、视频等。

触发方式：
    1. 消息里含 BV 号（如 BV1Fg411y79R 或 B站链接）-> 自动解析（封面 + 视频）
    2. 发送「B站解析」-> 显示使用教程
    3. 发送「下载视频/仅下载封面/仅下载视频/仅下载音频 (BV号或链接)」-> 只下载对应内容
    4. 隐藏功能：发送 BBDown 详细命令 -> 执行并发送下载的内容
    5. 发送「登录b站」/「登录b站tv」-> 生成扫码二维码，登录成功自动提示
"""

import logging
import os
import re
import shutil
import time

from bot.commands import register, ROLE_ALL, ROLE_ASSISTANT
from bot.core import tools
from config import ROOT, BBDOWN_DIR, BBDOWN_EXE, BBDOWN_COOKIE_SRC, FFMPEG_EXE

from . import bbdown_login

_log = logging.getLogger("bili")

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
· 登录b站  → 扫码登录B站网页账号喵
· 登录b站tv  → 扫码登录B站TV账号喵

注意：自动解析默认低画质（≤30MB），如需原画质请用「下载视频」喵"""

LOGIN_HINT = """检测到 BBDown 还没有登录 B站喵，解析画质会受到限制。
发送「登录b站」扫码登录（推荐），或「登录b站tv」登录TV账号喵。"""


def extract_bv(text):
    m = BV_RE.search(text or "")
    return m.group(0) if m else None


def _matcher(text):
    # 含 BV 号/B站链接 -> 自动解析；整条消息精确为「B站解析」-> 显示教程。
    # 用精确匹配避免「· 📺B站解析」「我要B站解析」等被子串误触发。
    if extract_bv(text):
        return True
    return (text or "").strip().lower() == "b站解析"


def _login_matcher(text):
    """「登录b站」/「登录b站tv」精确触发（大小写与空格宽松）。"""
    return _normalize_login(text) is not None


def _normalize_login(text):
    """把登录命令归一化：返回 'web' / 'tv' / None。

    接受：登录b站、B站登录、登录B站、登录b站tv、B站登录TV、登录b站电视 ...
    判据：去掉空格、标点与大小写差异后，**整条消息必须就是这条命令**
    （允许前缀语气词如「帮我」），避免「B站解析 登录b站」「为什么要登录b站」
    这类含命令词的句子被误触发。
    """
    t = (text or "").strip().lower()
    # 去空格与常见标点（含中英文全/半角，用户常带「！」「。」「，」等）
    for ch in (" \u3000\u2005\t\u00a0"          # 各类空白
               "!?~,.;:\u3001\u3002\uff01\uff1f\uff5e\uff0c\uff0e\uff1b\uff1a"
               "\u201c\u201d\u2018\u2019\"'"
               "()\uff08\uff09[]\u3010\u3011{}\u3014\u3015<>\u300c\u300d\u300e\u300f\u300a\u300b"):
        t = t.replace(ch, "")
    if not t:
        return None
    # 允许的礼貌前缀
    for pre in ("帮我", "请", "麻烦", "我要", "我想"):
        if t.startswith(pre):
            t = t[len(pre):]
            break
    # 去前缀后必须「以登录开头」或「以B站登录开头」，且不含其它多余内容
    patterns = [
        (r"^(?:登录|login)(?:b站|bilibili)(?:tv|电视)$", "tv"),
        (r"^(?:登录|login)(?:b站|bilibili)$", "web"),
        (r"^(?:b站|bilibili)(?:登录|login)(?:tv|电视)?$", None),  # 语序反转，按有无 tv 判定
    ]
    for pat, forced in patterns:
        m = re.match(pat, t)
        if m:
            if forced:
                return forced
            return "tv" if ("tv" in t or "电视" in t) else "web"
    return None


@register(keywords=["登录b站", "B站登录"], help="扫码登录B站账号（BBDown）喵",
          matcher=_login_matcher, role=ROLE_ASSISTANT, exact=True)
async def cmd_bili_login(ctx):
    """发送「登录b站」-> 扫码登录 WEB 账号；「登录b站tv」-> 登录 TV 账号。"""
    text = (ctx.args if ctx.args else "") or ""
    kind = _normalize_login(text) or "web"

    label = "B站网页账号" if kind == "web" else "B站TV账号"
    await ctx.reply_text(f"正在生成{label}登录二维码，请稍候喵…")

    # 登录成功后由后台任务回调通知（进程会一直等扫码）
    async def on_success(session, cred_path):
        name = os.path.basename(cred_path)
        await ctx.reply_text(
            f"✅ {label}登录成功喵！登录态已保存（{name}），现在解析画质不受限了喵～"
        )
        bbdown_login.drop_session(getattr(session, "session_id", None))

    async def on_fail(session, msg):
        await ctx.reply_text(f"⚠️ {msg}")
        bbdown_login.drop_session(getattr(session, "session_id", None))

    result = await bbdown_login.start_login(kind, on_success, on_fail)
    if not result.ok:
        await ctx.reply_text(f"❌ {result.message}")
        return
    await ctx.reply_text(result.message)
    if result.qrcode_path and os.path.isfile(result.qrcode_path):
        sent = await ctx.sender.send_local_file(ctx.message, 1, result.qrcode_path)
        if isinstance(sent, str):
            await ctx.reply_text(sent)


# 登录命令不受被动解析模式约束（群里也应能直接用）
cmd_bili_login.passive_gate = False


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
def _sync_bbdown_cookie():
    """把用户手动登录 BBDown 的登录态（BBDown.data / BBDownTV.data）同步到 bot 使用的 BBDown 目录。

    BBDown 从 exe 同目录读取 `.data`；bot 用的 tools/BBDown 若没有登录态，
    未登录解析受限（画质被压到 480P），严重时会被判为「解析失败」。
    WEB 与 TV 是两套独立登录态，文件不同（BBDown.data / BBDownTV.data），都要同步。
    cookie 过期后用户重新登录源目录即可自动续期。
    """
    try:
        if not BBDOWN_COOKIE_SRC or not BBDOWN_DIR:
            return
        for name in ("BBDown.data", "BBDownTV.data"):
            src = os.path.join(BBDOWN_COOKIE_SRC, name)
            dst = os.path.join(BBDOWN_DIR, name)
            if not os.path.isfile(src):
                continue
            if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
                continue
            shutil.copy2(src, dst)
            print(f"[bili] 已同步 BBDown 登录态: {dst}", flush=True)
    except Exception as e:
        _log.warning("同步 BBDown cookie 失败: %s", e)


async def _get_info(bv):
    """用 --only-show-info 获取视频信息，返回 dict 或 None。

    注意：必须显式 --ffmpeg-path。BBDown 1.6.3 在找不到 ffmpeg 时会**直接罢工**，
    连 --only-show-info 都不执行（返回码 1，只打印「找不到可执行的ffmpeg文件」），
    表现为「解析失败」。此前这里漏了该参数，是发送链接报解析失败的真正原因。
    """
    _sync_bbdown_cookie()
    out, err, code = await tools.run_script(
        f'"{BBDOWN_EXE}" {bv} --only-show-info --ffmpeg-path "{FFMPEG_EXE}"', timeout=60
    )
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
    # 未登录也会打印标题（只是画质受限），单独识别出来供上层提示
    info["not_login"] = bbdown_login.MARK_NOT_LOGIN in text
    if not info.get("title"):
        # 解析失败时打印原始输出，便于定位（BBDown 网络报错 / 编码问题等）
        print(f"[bili] 解析失败 bv={bv} rc={code} out={out[:300]!r} err={err[:300]!r}", flush=True)
        _log.warning("BBDown 解析失败: bv=%s rc=%s err=%r", bv, code, err[:300])
        return None
    if info["not_login"]:
        _log.info("BBDown 未登录，解析画质受限: bv=%s", bv)
    return info


# ---------- 下载 ----------
def _fresh_workdir():
    """创建本次下载的独立临时目录，返回路径。"""
    workdir = os.path.join(_TMP_ROOT, str(int(time.time() * 1000)))
    os.makedirs(workdir, exist_ok=True)
    return workdir


def _find_downloaded(workdir, exts):
    """在目录（含子目录）里找指定扩展名的文件，返回第一个匹配路径或 None。"""
    if not os.path.isdir(workdir):
        return None
    for root, _dirs, files in os.walk(workdir):
        for name in files:
            if name.lower().endswith(exts):
                return os.path.join(root, name)
    return None


async def _download_cover(bv, workdir):
    _sync_bbdown_cookie()
    await tools.run_script(f'"{BBDOWN_EXE}" {bv} --cover-only --ffmpeg-path "{FFMPEG_EXE}" --work-dir "{workdir}"', timeout=120)
    return _find_downloaded(workdir, (".png", ".jpg", ".jpeg", ".webp"))


async def _download_video(bv, workdir, low_quality=False):
    # 关键：必须显式 --ffmpeg-path 指定完整版 ffmpeg。BBDown 1.6.3 只在同目录或 PATH 找
    # ffmpeg，tools/BBDown/ 里没有 ffmpeg 时会落到 PATH 上 TRAE 的精简版，导致合并失败。
    _sync_bbdown_cookie()
    if low_quality:
        # 自动解析默认低画质：480P 优先 HEVC/AV1，控制体积便于群里直接点开看
        await tools.run_script(
            f'"{BBDOWN_EXE}" {bv} -p 1 -q "480P 清晰, 360P 流畅" -e "hevc,av1,avc" --ffmpeg-path "{FFMPEG_EXE}" --work-dir "{workdir}"',
            timeout=600,
        )
    else:
        await tools.run_script(f'"{BBDOWN_EXE}" {bv} -p 1 --ffmpeg-path "{FFMPEG_EXE}" --work-dir "{workdir}"', timeout=600)
    return _find_downloaded(workdir, (".mp4", ".mkv", ".flv", ".mov"))


async def _download_audio(bv, workdir):
    _sync_bbdown_cookie()
    await tools.run_script(f'"{BBDOWN_EXE}" {bv} --audio-only --ffmpeg-path "{FFMPEG_EXE}" --work-dir "{workdir}"', timeout=600)
    return _find_downloaded(workdir, (".m4a", ".mp3", ".flac", ".wav", ".aac"))


# ---------- 自动解析（封面 + 视频） ----------
async def _auto_parse(ctx, bv):
    await ctx.reply_text("🐟️ 正在投喂猫猫，请稍候喵…")

    info = await _get_info(bv)
    if not info:
        await ctx.reply("解析失败，请检查 BV 号是否正确")
        return

    # 未登录时解析会受限（画质被压到 480P）：提示一次并给出扫码登录入口
    if info.get("not_login"):
        await ctx.reply_text(LOGIN_HINT)

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
        _sync_bbdown_cookie()
        # 只允许 BBDown 开头的命令；把 BBDown 替换为完整路径。
        # 注意：替换串里含 Windows 反斜杠路径，必须用函数替换，否则 \B 等会被 re 当成非法转义
        cmd = re.sub(r"^BBDown\b", lambda m: f'"{BBDOWN_EXE}"', text, flags=re.IGNORECASE)
        if "--work-dir" not in cmd:
            cmd = f'{cmd} --work-dir "{workdir}"'
        # 显式指定完整版 ffmpeg，否则 BBDown 会用 PATH 上的精简版导致合并失败
        if "--ffmpeg-path" not in cmd:
            cmd = f'{cmd} --ffmpeg-path "{FFMPEG_EXE}"'
        await tools.run_script(cmd, timeout=600)

        # 只发送最相关的一个文件（视频 > 音频 > 封面）。
        # BBDown 即使 --video-only/--audio-only 也会默认下载封面，这里按优先级只发一个，避免多发一张图
        sent = False
        for exts, ftype in [
            ((".mp4", ".mkv", ".flv", ".mov"), 2),
            ((".m4a", ".mp3", ".flac", ".wav", ".aac"), 4),  # 音频以文件形式发送
            ((".png", ".jpg", ".jpeg", ".webp"), 1),
        ]:
            path = _find_downloaded(workdir, exts)
            if path:
                result = await ctx.sender.send_local_file(ctx.message, ftype, path)
                if isinstance(result, str):
                    await ctx.reply_text(result)
                sent = True
                break
        if not sent:
            await ctx.reply_text("命令执行完成，但没有找到可发送的文件")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
