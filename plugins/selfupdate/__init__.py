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
    · 只允许管理员触发（role=ROLE_ADMIN，2026-10-16 从 ASSISTANT 收紧）
    · 群聊里必须 @ 机器人（单聊放行）
    · 更新前自动备份当前 commit hash，pull 失败可回滚提示
    · pull 冲突时中止并报告，不强行覆盖本地修改
    · 触发后先回复再重启，保证用户能看到结果

★ 为什么要收紧到 ROLE_ADMIN（2026-10-16 实网事件）：
    webhook 事件回调本身不带身份凭证，攻击者可以 POST 假事件并**任意填 openid**，
    于是能冒充协助者触发更新/重启。★「强制更新」尤其危险 —— 它会
    `git add -A` + `git reset --hard origin/main`，把服务器上未入库的改动直接抹掉，
    且它不含 `; & |` 等元字符，命令注入那道拦截**拦不住它**。
    ★ 但收紧权限只是「缩小受影响的人」，真正堵住伪造事件的是 WEBHOOK_SIGNATURE。
"""

import asyncio
import os
import re
import subprocess
import sys
import time

from config import ROOT, PYTHON

from bot.commands import register, ROLE_ADMIN, ROLE_ASSISTANT, at_ok
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
    help="从 GitHub 拉取最新代码并重启（仅管理员，群内需 @）",
    role=ROLE_ADMIN,
    exact=True,
)
async def cmd_bot_update(ctx):
    """检查并应用 GitHub 上的更新，然后自动重启。"""
    if not at_ok(ctx):
        return  # 群里没 @ 就完全静默：避免旁人随手四个字把 bot 重启了
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
        # ★ 不列文件清单：改动一多就是刷屏（实测报过 61 个）。
        #   想看细节自己去服务器跑 `git status --porcelain`；
        #   想「保留改动照样更新」就发「强制更新」。
        await ctx.reply_text(
            f"⚠️ 本机有 {len(dirty)} 个文件改动未提交，为避免覆盖先不自动更新喵。\n"
            "· 想保留这些改动并强制更新 → 发「强制更新」\n"
            "· 想先看是什么 → 服务器上 `git status --porcelain`"
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


@register(
    keywords=["强制更新", "提交并更新", "bot强制更新", "强制bot更新"],
    help="把本机改动先归档成 commit，再强制对齐 GitHub 最新代码并重启",
    role=ROLE_ADMIN,
    exact=True,
)
async def cmd_bot_update_force(ctx):
    """「提交并更新」：本机改动一个都不丢，但照样能更新。

    与普通更新的区别：普通更新一看到工作区脏就中止；这条先把脏改动
    `git add -A` + commit 成一个**快照提交**，再 `git reset --hard origin/main`
    硬对齐远程 —— 所以既不会被拦，也不会产生冲突标记（普通 pull 合并时可能留标记）。

    ★ 要注意：reset --hard 会让工作区与远程完全一致 ⇒
      「main 里没有、但被这次归档进快照」的文件会从工作区消失
      （服务器上的 .bak / tar.gz 之类）。它们仍留在快照 commit 里，
      要找回用 `git checkout <快照sha> -- <文件>`。
      .gitignore 里的东西（settings.json、data/、插件大素材）不受影响。
    """
    if not at_ok(ctx):
        return  # 群里没 @ 就完全静默：这条会 reset --hard，更不能让别人随手触发
    if not _is_git_repo():
        await ctx.reply_text("呜…当前目录不是 git 仓库，没法强制更新喵。")
        return
    branch = _current_branch()
    if branch != _BRANCH:
        await ctx.reply_text(
            f"呜…当前在 `{branch}` 分支，不是我预期的 `{_BRANCH}`，先不动喵。"
        )
        return

    await ctx.reply_text("🔧 强制更新：先归档本机改动，再对齐 GitHub 最新代码喵…")

    code, out, err = _git("fetch", "--prune", _REMOTE, timeout=_GIT_TIMEOUT)
    if code != 0:
        await ctx.reply_text(
            f"呜…连不上 GitHub，强制更新中止（什么都没改）喵。\n```\n{(err or out)[:300]}\n```"
        )
        return
    code, remote, err = _git("rev-parse", f"{_REMOTE}/{_BRANCH}")
    if code != 0 or not remote:
        await ctx.reply_text(f"呜…读不到远程分支 `{_REMOTE}/{_BRANCH}` 喵。")
        return

    # 1) 归档：已改动 + 未跟踪的文件统统存成一个 commit（保证不丢）
    snap = ""
    dirty = _dirty_files()
    if dirty:
        code, _, err = _git("add", "-A", timeout=120)
        if code != 0:
            await ctx.reply_text(f"呜…归档失败（git add）喵。\n```\n{err[:300]}\n```")
            return
        ts = time.strftime("%Y-%m-%d %H:%M")
        code, out, err = _git(
            "-c", "user.name=qqbot", "-c", "user.email=qqbot@local",
            "commit", "-m",
            f"chore(自动): 强制更新前的本机快照 {ts}（{len(dirty)} 项）",
            timeout=60,
        )
        if code != 0:
            await ctx.reply_text(
                f"呜…归档失败（git commit）喵。\n```\n{(err or out)[:300]}\n```"
            )
            return
        snap = _short(_local_head())

    # 2) 依赖清单是否变化（对齐前后比对）
    _, req_before, _ = _git("rev-parse", "HEAD:requirements.txt")

    # 3) 硬对齐远程最新代码
    code, out, err = _git("reset", "--hard", remote, timeout=180)
    if code != 0:
        tip = f"本机改动已安全归档为 {snap}，可用 `git reset --hard {snap}` 还原。" if snap else ""
        await ctx.reply_text(f"呜…强制对齐失败喵。{tip}\n```\n{(err or out)[:300]}\n```")
        return

    _, req_after, _ = _git("rev-parse", "HEAD:requirements.txt")
    note = ""
    if (req_before or "") != (req_after or ""):
        rc, o, _ = await _sh(
            f'"{PYTHON or sys.executable}" -m pip install -r requirements.txt',
            timeout=600,
        )
        note = "\n📚 依赖已更新" if rc == 0 else \
            f"\n⚠️ 依赖安装失败（{rc}），请手动跑 pip install -r requirements.txt"

    code, sig, _ = _git("log", "-1", "--format=%h %s", "HEAD")
    lines = ["🎉 强制更新完成喵！", f"当前版本：{sig[:120]}"]
    if snap:
        lines.append(
            f"· 原来那 {len(dirty)} 项改动已归档为 {snap}"
            f"（要找回：`git checkout {snap} -- <文件>`）"
        )
    else:
        lines.append("· 本机本来就是干净的，直接对齐远程")
    lines.append("· main 里没有的旧文件（.bak / tar.gz 之类）已从工作区移除，需要的话从上面快照取回")
    lines.append(f"即将重启…{note}")
    await ctx.reply_text("\n".join(lines))
    asyncio.get_event_loop().create_task(_delayed_restart(ctx))


# ---------------------------------------------------------------- 版本查询
@register(
    keywords=["bot版本", "喵喵版本", "版本"],
    help="查看当前运行版本与是否有更新（仅管理员/协助者）",
    # 只读查询，不改动任何东西 ⇒ 保持 ASSISTANT，不跟着收紧到 ADMIN
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
