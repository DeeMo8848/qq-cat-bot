# -*- coding: utf-8 -*-
"""启动（重启）提醒。

需求与背景：
    用户重启 bot 后没有任何反馈，只能自己发条命令确认「到底重启成功了没」。
    所以要在启动完成后主动发一条「重启完成」消息。

但**不能给所有群都发** —— 那会打扰所有群。用户给了两个可接受的方案：
    1. 「哪个群触发了重启命令，就发给哪个群」（默认，最贴近直觉）
    2. 「仅发送给 bot 主人」

因此本模块支持三种模式（存在 state.json 的 boot_notify 字段）：
    trigger : 发给「触发重启的那个会话」（群/私聊）。没有记录时不发。
    owner   : 只发给 bot 主人（settings.json 的 BOT_OWNER，缺省取 BOT_ADMINS[0]）。
    both    : 触发会话 + 主人 都发。
    off     : 关闭。

跨重启传递「是谁触发的」：
    重启会把进程整个换掉，内存里的信息全丢，所以触发时先把
    `{scene, target, at, reason}` 写到 tmp/boot_notify.json，
    新进程启动完成后读取并按模式发送，然后删除该文件（避免重复发送）。
    为防「重启失败后旧文件一直留着」导致误发，文件带 TTL（默认 10 分钟）。
"""

import json
import logging
import os
import time

from config import ROOT

_log = logging.getLogger("ops.boot")

# 重启来源的暂存文件（跨进程传递）
_PENDING_FILE = os.path.join(ROOT, "tmp", "boot_notify.json")

# 暂存文件的有效期：超过则认为不是「刚刚这次重启」留下的，直接忽略
PENDING_TTL = 600

# 允许的模式
MODES = ("off", "trigger", "owner", "both")
DEFAULT_MODE = "trigger"


# ---------------------------------------------------------------- 配置读写
def get_mode() -> str:
    """读取启动提醒模式（默认 trigger）。"""
    try:
        from bot.core import state
        mode = (state.load_state().get("boot_notify") or DEFAULT_MODE)
        mode = str(mode).strip().lower()
        return mode if mode in MODES else DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def set_mode(mode: str) -> str:
    """写入启动提醒模式，返回规范化后的值。"""
    mode = str(mode or "").strip().lower()
    if mode not in MODES:
        mode = DEFAULT_MODE
    from bot.core import state
    s = state.load_state()
    s["boot_notify"] = mode
    state.save_state(s)
    return mode


# ---------------------------------------------------------------- 主人 openid
def get_owner_openid() -> str:
    """取 bot 主人的 openid。

    优先 settings.json 的 BOT_OWNER；没有则退化为 BOT_ADMINS 的第一个。
    """
    try:
        from config import _cfg, BOT_ADMINS
        v = _cfg("BOT_OWNER", "")
        if v:
            return str(v).strip()
        if BOT_ADMINS:
            return str(BOT_ADMINS[0]).strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 触发侧
def remember_trigger(ctx, reason: str = "重启bot"):
    """在发起重启前，记下「是谁触发的」，供新进程启动后回发。

    只在模式需要时才写盘（off 模式没必要留痕）。
    """
    if get_mode() == "off":
        return
    try:
        scene = getattr(ctx, "scene", None)
        target = getattr(ctx, "target", None)
        if not scene or not target:
            return
        os.makedirs(os.path.dirname(_PENDING_FILE), exist_ok=True)
        with open(_PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "scene": scene,
                "target": target,
                "reason": reason,
                "at": time.time(),
            }, f, ensure_ascii=False, indent=2)
        _log.info("已记录重启来源 %s/%s，重启完成后将回发提醒", scene, target)
    except Exception as e:
        _log.warning("记录重启来源失败: %s", e)


def _read_pending():
    """读取重启来源；文件不存在 / 过期 / 损坏都返回 None。"""
    try:
        if not os.path.isfile(_PENDING_FILE):
            return None
        with open(_PENDING_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        if time.time() - float(d.get("at") or 0) > PENDING_TTL:
            _log.info("重启来源记录已过期（>%ds），忽略", PENDING_TTL)
            return None
        if not d.get("scene") or not d.get("target"):
            return None
        return d
    except Exception as e:
        _log.warning("读取重启来源失败: %s", e)
        return None


def _clear_pending():
    try:
        if os.path.isfile(_PENDING_FILE):
            os.remove(_PENDING_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- 启动侧
def _build_text(pending=None) -> str:
    """构造提醒文本（含版本与运行时长，方便一眼确认重启成功）。

    这里刻意不 import plugins.ops：ops 依赖 bot.commands，反向引用会形成环。
    需要的两条信息（当前提交、运行时长）在本模块内轻量实现即可。
    """
    lines = ["✅ 重启完成喵，我已经上线啦～"]
    commit = _current_commit()
    if commit:
        lines.append("📦 版本：%s" % commit[:120])
    up = _uptime_text()
    if up:
        lines.append("⏱️ 服务已运行：%s" % up)
    if pending and pending.get("reason"):
        lines.append("🔄 来源：%s" % pending["reason"])
    lines.append("发送「菜单」可以看看我能做什么喵")
    return "\n".join(lines)


def _current_commit() -> str:
    """当前代码的短提交号 + 说明（非 git 仓库返回空串）。"""
    import subprocess
    import sys
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0
        res = subprocess.run(
            ["git", "-C", ROOT, "log", "-1", "--format=%h %s"],
            capture_output=True, timeout=10, creationflags=flags,
        )
        return (res.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _uptime_text() -> str:
    """服务运行时长。仅 Linux 有 systemd 时可取，其它环境返回空串。"""
    try:
        if os.name != "posix":
            return ""
        from plugins.ops import _uptime_seconds, _fmt_duration
        return _fmt_duration(_uptime_seconds())
    except Exception:
        return ""


async def send_boot_notify(sender, api=None):
    """启动完成后调用：按配置给对应会话发送「重启完成」提醒。

    sender: 已初始化的 Sender 实例
    失败只记日志，绝不影响 bot 启动。
    """
    mode = get_mode()
    if mode == "off":
        _log.info("启动提醒已关闭，跳过")
        return

    pending = _read_pending()
    text = _build_text(pending)

    targets = []   # [(scene, target, label)]

    if mode in ("trigger", "both") and pending:
        targets.append((pending["scene"], pending["target"], "触发会话"))

    if mode in ("owner", "both"):
        owner = get_owner_openid()
        if owner:
            targets.append(("c2c", owner, "主人"))
        else:
            _log.warning("启动提醒模式=%s 但未配置 BOT_OWNER / BOT_ADMINS，跳过主人提醒", mode)

    if not targets:
        _log.info("启动提醒：没有可发送的目标（模式=%s，pending=%s）", mode, bool(pending))
        _clear_pending()
        return

    ok_count = 0
    for scene, target, label in targets:
        try:
            s = sender
            if s is None and api is not None:
                from bot.core.sender import Sender
                s = Sender(api)
            if s is None:
                _log.warning("启动提醒无可用 Sender，跳过 %s(%s/%s)", label, scene, target)
                continue
            await s._send(scene, target, msg_type=0, content=text)
            ok_count += 1
            print("[ops] 已发送启动提醒 -> %s(%s/%s)" % (label, scene, target), flush=True)
        except Exception as e:
            _log.warning("发送启动提醒到 %s(%s/%s) 失败: %s", label, scene, target, e)

    if ok_count:
        _clear_pending()
    else:
        _log.warning("启动提醒全部发送失败，保留暂存文件以便下次重试")
