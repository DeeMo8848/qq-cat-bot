# -*- coding: utf-8 -*-
"""bot 自更新命令：从 GitHub 拉取最新代码并自动重启。

触发词：bot更新 / 喵喵更新 / 更新bot / bot update（仅管理员 + 协助者）

流程：
    1. 检查是否有新提交（git fetch + 比对本地/远程 HEAD）
    2. 无更新 -> 直接回复「已是最新」
    3. 有更新 -> git pull；若 requirements.txt 有变化则顺带 pip install
    4. 回复更新摘要 -> 延迟 2 秒重启进程

重启方式（跨平台，见 bot/core/platform.py）：
    Linux  : os.execv 原地替换进程（PID 不变，systemd 不会认为服务退出）
    Windows: os._exit 退出，由「启动bot.bat」的循环 / 守护脚本拉起

安全设计：
    · 只允许管理员与协助者触发（role=ROLE_ASSISTANT）
    · 更新前自动备份当前 commit hash，pull 失败可回滚提示
    · pull 冲突时中止并报告，不强行覆盖本地修改
    · 触发后先回复再重启，保证用户能看到结果
"""

import asyncio
import os
import re
import subprocess
import sys
import time

from config import ROOT, PYTHON

from bot.commands import register, ROLE_ASSISTANT
from bot.core import platform as _plat

# 重启延迟：留出时间让「更新完成」的消息送达 QQ
_RESTART_DELAY = 2.0

# git 命令超时（秒）—— 网络慢时 pull 可能较久
_GIT_TIMEOUT = 180

_REMOTE = "origin"
_BRANCH = "main"


def _git(*args, timeout: int = 60):
    """在项目根执行 git 命令，返回 (returncode, stdout, stderr)。

    用 -c core.quotepath=false 让中文文件名正常显示，不做引号转义。
    """
    try:
        res = subprocess.run(
            ["git", "-C", ROOT, "-c", "core.quotepath=false", *args],
            capture_output=True, timeout=timeout,
        )
        out = res.stdout.decode("utf-8", "replace").strip()
        err = res.stderr.decode("utf-8", "replace").strip()
        return res.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", f"git 命令超时（{timeout}s）"
    except FileNotFoundError:
        return -2, "", "未找到 git 命令，请先安装 git"
    except Exception as e:
        return -3, "", f"{type(e).__name__}: {e}"


def _is_git_repo() -> bool:
    code, out, _ = _git("rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def _local_head() -> str:
    code, out, _ = _git("rev-parse", "HEAD")
    return out if code == 0 else ""


def _current_branch() -> str:
    code, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


def _short(sha: str) -> str:
    return (sha or "")[:7]


async def _sh(cmd: str, timeout: int = _GIT_TIMEOUT):
    """异步执行 shell 命令，返回 (rc, stdout, stderr)。"""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=ROOT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"命令超时（{timeout}s）"
    text = (out or b"").decode("utf-8", "replace").strip()
    return proc.returncode, text, ""


def _dirty_files():
    """返回工作区未提交的改动列表（用于 pull 前判断是否会被覆盖）。"""
    code, out, _ = _git("status", "--porcelain")
    if code != 0 or not out:
        return []
    return [l for l in out.splitlines() if l.strip()]


def _restart(reason: str = "更新完成"):
    """重启进程：Linux 原地替换，Windows 退出由外部拉起。"""
    print(f"[更新] 准备重启：{reason}", flush=True)
    # 确保日志先落盘，再动进程
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if _plat.can_exec_self():
        # Linux：execv 原地替换，PID 不变，systemd 无感
        entry = os.path.join(ROOT, "main.py")
        _plat.exec_self(PYTHON or sys.executable, entry, ROOT)
        return
    # Windows：退出进程，交给启动脚本循环 / 守护脚本重启
    os._exit(0)


async def _delayed_restart(ctx):
    """延迟重启：先让上一条消息送达 QQ，再重启。"""
    await asyncio.sleep(_RESTART_DELAY)
    _restart("命令触发")


@register(
    keywords=["bot更新", "喵喵更新", "更新bot", "bot upgrade"],
    help="从 GitHub 拉取最新代码并重启（仅管理员/协助者）",
    role=ROLE_ASSISTANT,
    exact=True,
)
async def cmd_bot_update(ctx):
    """检查并应用 GitHub 上的更新，然后自动重启。"""
    if not _is_git_repo():
        await ctx.reply_text(
            "呜…当前目录不是 git 仓库，没法自动更新喵。\n"
            "请先用 deploy/install.sh 安装，或手动 git clone 后再用本命令。"
        )
        return

    branch = _current_branch()
    if branch != _BRANCH:
        await ctx.reply_text(
            f"呜…当前在 `{branch}` 分支，不是我预期的 `{_BRANCH}`，先不自动更新喵。\n"
            f"要切回请执行：git checkout {_BRANCH}"
        )
        return

    await ctx.reply_text("🔍 正在检查更新，请稍候喵…")

    # 1) 拉取远程信息（不改工作区）
    code, out, err = _git("fetch", "--prune", _REMOTE, timeout=_GIT_TIMEOUT)
    if code != 0:
        await ctx.reply_text(
            "呜…连不上 GitHub，检查更新失败了喵。\n"
            f"```\n{(err or out)[:400]}\n```"
        )
        return

    local = _local_head()
    code, remote, err = _git("rev-parse", f"{_REMOTE}/{_BRANCH}")
    if code != 0 or not remote:
        await ctx.reply_text(f"呜…读不到远程分支 `{_REMOTE}/{_BRANCH}` 喵。")
        return

    if local == remote:
        code, sig, _ = _git("log", "-1", "--format=%h %s", "HEAD")
        await ctx.reply_text(f"✅ 已经是最新版了喵～\n当前：{sig[:120]}")
        return

    # 2) 统计将要拉取的提交
    code, log, _ = _git(
        "log", "--oneline", "--no-decorate", f"{local}..{remote}", timeout=60
    )
    commits = [l for l in log.splitlines() if l.strip()]
    n = len(commits)
    preview = "\n".join(commits[:8])
    more = f"\n…等共 {n} 个提交" if n > 8 else ""

    # 3) 工作区有本地改动时，避免 pull 被覆盖
    dirty = _dirty_files()
    if dirty:
        listing = "\n".join(dirty[:10])
        extra = f"\n…等 {len(dirty)} 个文件" if len(dirty) > 10 else ""
        await ctx.reply_text(
            f"⚠️ 检测到本机有 {len(dirty)} 个文件改动未提交，为避免覆盖先不自动更新喵：\n"
            f"```\n{listing}{extra}\n```\n"
            "请先提交或还原这些改动，再发一次更新命令。"
        )
        return

    await ctx.reply_text(
        f"📦 发现 {n} 个新提交，开始更新喵…\n```\n{preview}{more}\n```"
    )

    # 4) 记录 requirements.txt 是否变化（决定要不要重装依赖）
    code, req_before, _ = _git("rev-parse", "HEAD:requirements.txt")
    req_before = req_before if code == 0 else ""

    # 5) 执行 pull
    code, out, err = _git("pull", "--ff-only", _REMOTE, _BRANCH, timeout=_GIT_TIMEOUT)
    if code != 0:
        await ctx.reply_text(
            "呜…更新失败，已保持原样喵。\n"
            f"```\n{(err or out)[:500]}\n```\n"
            f"如需手动处理：`git pull --ff-only {_REMOTE} {_BRANCH}`"
        )
        return

    # 6) 依赖有变化则重装
    note = ""
    code, req_after, _ = _git("rev-parse", "HEAD:requirements.txt")
    req_after = req_after if code == 0 else ""
    if req_before != req_after:
        note = "\n📚 依赖清单有变化，正在安装新依赖…"
        await ctx.reply_text(f"✅ 代码已更新到 {_short(remote)}" + note)
        rc, o, _ = await _sh(
            f'"{PYTHON or sys.executable}" -m pip install -r requirements.txt',
            timeout=600,
        )
        if rc == 0:
            note = "\n📚 依赖已更新完成喵。"
        else:
            note = f"\n⚠️ 依赖安装失败（返回码 {rc}），重启后可能报错，请手动跑 pip install -r requirements.txt"

    code, sig, _ = _git("log", "-1", "--format=%h %s", "HEAD")
    await ctx.reply_text(
        f"🎉 更新完成喵！\n当前版本：{sig[:140]}{note}\n即将重启…"
    )
    asyncio.get_event_loop().create_task(_delayed_restart(ctx))


# ---------------------------------------------------------------- 版本查询
@register(
    keywords=["bot版本", "喵喵版本", "版本"],
    help="查看当前运行版本与是否有更新（仅管理员/协助者）",
    role=ROLE_ASSISTANT,
    exact=True,
)
async def cmd_bot_version(ctx):
    """只读检查：显示当前版本与远程是否一致，不做任何修改。"""
    if not _is_git_repo():
        await ctx.reply_text("当前不是 git 仓库，无法查看版本喵。")
        return
    code, sig, _ = _git("log", "-1", "--format=%h %ad %s", "--date=format:%Y-%m-%d %H:%M")
    plat = _plat.describe()
    lines = [
        "🐱 当前版本",
        f"提交：{sig[:140]}",
        f"分支：{_current_branch()}",
        f"系统：{plat['os']} / Python {plat['python']}",
    ]
    code, out, _ = _git("fetch", "--prune", _REMOTE, timeout=30)
    if code == 0:
        local = _local_head()
        code, remote, _ = _git("rev-parse", f"{_REMOTE}/{_BRANCH}")
        if code == 0 and remote and local != remote:
            code, log, _ = _git("log", "--oneline", f"{local}..{remote}")
            cnt = len([l for l in log.splitlines() if l.strip()])
            lines.append(f"状态：🆕 有 {cnt} 个新提交（发「bot更新」即可更新）")
        elif remote:
            lines.append("状态：✅ 已是最新")
    else:
        lines.append("状态：⚠️ 无法连接 GitHub 检查更新")
    await ctx.reply_text("\n".join(lines))
