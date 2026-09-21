# -*- coding: utf-8 -*-
"""运维命令：查看 bot 服务状态、重启 bot 服务。

触发词：
    bot状态 / 喵喵状态 / 服务状态 / bot status     → 查看运行状态
    重启bot / 重启喵喵 / bot重启 / bot restart     → 重启服务

设计要点：
    · 与「bot更新」同级权限（仅管理员 + 协助者），避免群友随手把服务搞挂
    · 重启优先走 systemd（`systemctl restart <unit>`），
      没有 systemd 或没权限时自动降级为 os.execv 原地替换进程
    · 状态输出保持精简：一行 is-active + 运行时长 + 当前提交
    · 重启前先回复再延迟执行，保证用户能看到结果（同 bot更新 的做法）

为什么重启要「优先 systemd」而不是直接 execv：
    execv 虽然 PID 不变、systemd 也无感，但**绕过了 systemd 的启动参数校验**。
    如果问题是「unit 文件写错 / 依赖没装」，execv 会一直失败重试，
    而 systemctl restart 会明确报出失败原因，并把服务状态标记为 failed 便于排查。
"""

import asyncio
import os
import re
import shutil
import subprocess
import sys

from config import ROOT, PYTHON, _cfg

from bot.commands import register, ROLE_ASSISTANT
from bot.core import platform as _plat

# 重启前留出的时间：够「正在重启」的消息送达 QQ
_RESTART_DELAY = 2.0

# systemd 服务单元名（可在 settings.json 用 SYSTEMD_UNIT 覆盖）
SYSTEMD_UNIT = _cfg("SYSTEMD_UNIT", "qqbot")

# 外部命令超时（秒）
_CMD_TIMEOUT = 20


# Windows 上避免子进程弹黑窗（非 Windows 传 0 等同无此参数）
_CREATE_NO_WINDOW = 0x08000000 if _plat.IS_WINDOWS else 0


def _run(cmd, timeout: int = _CMD_TIMEOUT):
    """执行外部命令，返回 (rc, 合并后的输出文本)。失败一律返回 rc=-1，不抛异常。"""
    try:
        res = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        out = (res.stdout or b"").decode("utf-8", "replace")
        err = (res.stderr or b"").decode("utf-8", "replace")
        return res.returncode, (out + err).strip()
    except FileNotFoundError:
        return -1, "命令不存在"
    except subprocess.TimeoutExpired:
        return -1, "命令超时"
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)


def _systemctl(*args, timeout: int = _CMD_TIMEOUT):
    """调用 systemctl。非 Linux / 无 systemctl 时返回 (None, 原因)。"""
    if not _plat.IS_LINUX:
        return None, "非 Linux 环境"
    exe = shutil.which("systemctl") or "/usr/bin/systemctl"
    if not os.path.isfile(exe):
        return None, "未安装 systemctl"
    rc, out = _run([exe, *args], timeout=timeout)
    return rc, out


def _uptime_seconds() -> int:
    """取服务已运行的秒数；取不到返回 0。

    用 **monotonic 时钟** 而不是解析 ActiveEnterTimestamp 的本地化文本：
        ActiveEnterTimestampMonotonic 是「服务进入 active 时系统已运行的微秒数」
        /proc/uptime 第一列是「系统已运行的秒数」
        两者相减即为服务运行时长 —— 与时区、locale 完全无关，
        不必依赖 `date -d` 去解析 "Mon 2026-09-21 03:04:52 CST" 这种串。
    """
    rc, out = _systemctl("show", SYSTEMD_UNIT, "-p", "ActiveEnterTimestampMonotonic")
    if rc != 0 or not out:
        return 0
    m = re.search(r"ActiveEnterTimestampMonotonic=(\d+)", out)
    if not m:
        return 0
    mono_us = int(m.group(1))
    if mono_us <= 0:
        # 0 表示服务未处于 active（inactive/failed 时该字段为 0）
        return 0
    try:
        with open("/proc/uptime", "r", encoding="ascii") as f:
            up_s = float(f.read().split()[0])
    except Exception:
        return 0
    return max(0, int(up_s - mono_us / 1_000_000.0))


def _fmt_duration(secs: int) -> str:
    """把秒数格式化成「3天2小时15分」，去掉为空的高位单位。"""
    if secs <= 0:
        return ""
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    mnt = rem // 60
    parts = []
    if d:
        parts.append("%d天" % d)
    if h:
        parts.append("%d小时" % h)
    if mnt or not parts:
        parts.append("%d分" % mnt)
    return "".join(parts)


def _current_commit() -> str:
    """当前代码的短提交号 + 提交说明（非 git 仓库时返回空串）。"""
    try:
        res = subprocess.run(
            ["git", "-C", ROOT, "log", "-1", "--format=%h %s"],
            capture_output=True, timeout=10, creationflags=_CREATE_NO_WINDOW,
        )
        return (res.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _restart_via_systemd() -> tuple[bool, str]:
    """用 systemctl 重启服务。返回 (是否成功, 说明)。"""
    # --no-block：不等服务真正起来就返回，避免 QQ 侧等太久超时
    rc, out = _systemctl("restart", SYSTEMD_UNIT, timeout=_CMD_TIMEOUT)
    if rc is None:
        return False, out                      # 非 Linux / 无 systemctl
    if rc == 0:
        return True, "systemctl restart %s" % SYSTEMD_UNIT
    return False, "systemctl 返回 %d：%s" % (rc, (out or "")[:200])


def _restart_via_execv() -> bool:
    """降级方案：原地替换进程（Linux 无 systemd 时），或退出交给外部守护（Windows）。"""
    if _plat.can_exec_self():
        entry = os.path.join(ROOT, "main.py")
        _plat.exec_self(PYTHON or sys.executable, entry, ROOT)
        return True
    # Windows：没有 execv，直接退出，由「启动bot.bat」的循环拉起
    os._exit(0)


async def _do_restart(ctx):
    """延迟片刻后重启：先让「正在重启」的消息送达，再动进程。"""
    await asyncio.sleep(_RESTART_DELAY)

    # 记下「是谁触发的重启」——新进程起来后会按这个记录回发「重启完成」提醒。
    # 必须在进程被换掉之前写盘（这里已经是重启前最后一刻，但仍在当前进程内，
    # 写盘发生在下面的 systemctl/execv 之前，安全）。
    try:
        from bot.core import boot_notify
        boot_notify.remember_trigger(ctx, reason="重启bot 命令")
    except Exception as e:
        print("[运维] 记录重启来源失败: %s" % e, flush=True)

    ok, detail = _restart_via_systemd()
    if ok:
        # systemd 已接管重启，当前进程随后会被 systemd 杀掉，无需再做别的
        print("[运维] 已下发 systemctl restart %s" % SYSTEMD_UNIT, flush=True)
        return

    print("[运维] systemd 重启不可用（%s），降级为进程自替换" % detail, flush=True)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    _restart_via_execv()


# ---------------------------------------------------------------- bot状态
@register(
    keywords=["bot状态", "喵喵状态", "服务状态", "bot status"],
    help="查看 bot 服务运行状态（仅管理员/协助者）",
    role=ROLE_ASSISTANT,
    exact=True,
)
async def cmd_bot_status(ctx):
    """精简状态：服务是否在跑 + 运行时长 + 当前版本。"""
    lines = []

    rc, out = _systemctl("is-active", SYSTEMD_UNIT)
    if rc is None:
        # 非 Linux：没有 systemd，只能报告进程本身活着
        lines.append("🤖 服务状态：运行中（无 systemd，进程直跑）")
    else:
        state = (out or "").strip().splitlines()[0] if (out or "").strip() else "unknown"
        icon = {"active": "✅", "activating": "🔄", "deactivating": "🔄",
                "failed": "❌", "inactive": "⚪"}.get(state, "❓")
        lines.append("🤖 服务状态：%s %s" % (icon, state))

        # 只在服务活着时才问运行时长，省一次子进程调用
        if state == "active":
            up = _fmt_duration(_uptime_seconds())
            if up:
                lines.append("⏱️ 已运行：%s" % up)

    commit = _current_commit()
    if commit:
        lines.append("📦 当前版本：%s" % commit[:120])

    await ctx.reply_text("\n".join(lines))


# ---------------------------------------------------------------- 重启bot
@register(
    keywords=["重启bot", "重启喵喵", "bot重启", "bot restart"],
    help="重启 bot 服务（仅管理员/协助者）",
    role=ROLE_ASSISTANT,
    exact=True,
)
async def cmd_bot_restart(ctx):
    """重启服务：优先 systemctl，不可用时降级为进程自替换。"""
    # 先探测 systemd 可用性，把「用什么方式重启」提前告诉用户
    if _plat.IS_LINUX and shutil.which("systemctl"):
        way = "systemd（systemctl restart %s）" % SYSTEMD_UNIT
    elif _plat.can_exec_self():
        way = "进程自替换（execv）"
    else:
        way = "退出进程，由启动脚本重新拉起"

    await ctx.reply_text("🔄 正在重启喵…（%s）" % way)
    asyncio.get_event_loop().create_task(_do_restart(ctx))


# 供 Web 后台「测试功能」模块使用（命令名集合，用于隐藏与总开关级联）
OPS_CMD_NAMES = {"cmd_bot_status", "cmd_bot_restart", "cmd_boot_notify"}


# ---------------------------------------------------------------- 设定重启提醒
@register(
    keywords=["重启提醒", "启动提醒", "开机提醒"],
    help="设置重启完成提醒的发送范围（仅管理员/协助者）",
    role=ROLE_ASSISTANT,
)
async def cmd_boot_notify(ctx):
    """查看 / 设置「重启完成」提醒发给谁。

    用法：
        重启提醒            -> 查看当前设置
        重启提醒 群         -> 发给触发重启的那个群/会话（默认）
        重启提醒 主人       -> 只发给 bot 主人
        重启提醒 都发       -> 触发会话 + 主人都发
        重启提醒 关闭       -> 关闭提醒
    """
    from bot.core import boot_notify as BN

    arg = (getattr(ctx, "args", "") or "").strip()
    # args 里带的是触发词之后的内容；兼容直接写整句
    for kw in ("重启提醒", "启动提醒", "开机提醒"):
        if arg.startswith(kw):
            arg = arg[len(kw):].strip()

    if not arg:
        cur = BN.get_mode()
        owner = BN.get_owner_openid()
        names = {"off": "关闭", "trigger": "发给触发提醒的会话", "owner": "只发给 bot 主人",
                 "both": "触发会话 + 主人"}
        lines = ["🔔 重启提醒设置",
                 "当前：%s（%s）" % (names.get(cur, cur), cur),
                 "",
                 "回复以下内容可修改：",
                 "· 重启提醒 群    → 发给触发重启的群/会话（推荐）",
                 "· 重启提醒 主人  → 只发给 bot 主人",
                 "· 重启提醒 都发  → 两者都发",
                 "· 重启提醒 关闭  → 不再提醒"]
        if owner:
            lines.append("")
            lines.append("（当前主人 openid：%s…）" % owner[:8])
        else:
            lines.append("")
            lines.append("⚠️ 未配置 BOT_OWNER，选「主人」模式前请先在后台填好主人 openid")
        await ctx.reply_text("\n".join(lines))
        return

    mapping = {
        "群": "trigger", "触发": "trigger", "trigger": "trigger",
        "这个群": "trigger", "本群": "trigger",
        "主人": "owner", "owner": "owner", "仅主人": "owner",
        "都发": "both", "both": "both", "全部": "both",
        "关闭": "off", "关": "off", "off": "off", "不要": "off",
    }
    mode = mapping.get(arg.lower())
    if mode is None:
        await ctx.reply_text("没听懂喵…可选：群 / 主人 / 都发 / 关闭")
        return
    if mode in ("owner", "both") and not BN.get_owner_openid():
        await ctx.reply_text("⚠️ 还没配置 bot 主人 openid（settings.json 的 BOT_OWNER）喵，"
                             "先让管理员在后台填好再试～")
        return
    BN.set_mode(mode)
    names = {"off": "已关闭", "trigger": "重启后发给触发的那个群/会话",
             "owner": "重启后只发给 bot 主人", "both": "重启后触发会话与主人都发"}
    await ctx.reply_text("✅ 已设置：%s喵" % names[mode])
