# -*- coding: utf-8 -*-
"""🃏 cardforge 调用器：异步子进程 + 串行队列（前一个制作完成才开始下一个）。

- cardforge 目录解析：settings.json 的 CARD_DIR 显式指定 → tools/cardforge → 旧路径
- 输出隔离：每张卡用独立 cardKey 作为 slug，产物进入 data/cards/assets/<cardKey>/
- 串行队列：asyncio 单 worker 逐个消费，前面的卡没做完不会开始下一张
"""

import asyncio
import json
import logging
import os
import shutil

from config import ROOT

_log = logging.getLogger("cards.forge")

TOOLS_DIR = os.path.join(ROOT, "tools")
_LEGACY_DIR = r"D:\略夹\ai\ai工具\cardforge"

# 素材类型 → cardforge assets 子目录
_CAT_DIRS = {
    "background": "backgrounds",
    "frame": "frames",
    "seal": "seals",
    "back": "backs",
}
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# 内置辉光（边框特效）清单
GLOW_BUILTINS = ["暖金描边", "冰蓝冷光", "紫罗兰夜光", "四角聚焦", "上下双色",
                 "菲涅尔描边", "双环辉光", "RGB变色灯光", "霓虹灯", "发廊螺纹", "樱花粉"]


def _resolve_dir() -> str:
    try:
        with open(os.path.join(ROOT, "settings.json"), "r", encoding="utf-8") as f:
            d = (json.load(f).get("CARD_DIR") or "").strip()
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    cand = os.path.join(TOOLS_DIR, "cardforge")
    if os.path.isdir(cand):
        return cand
    if os.path.isdir(_LEGACY_DIR):
        return _LEGACY_DIR
    return cand  # 兜底返回 tools 路径，调用时给出明确报错


CARDFORGE_DIR = _resolve_dir()


def cardforge_ready() -> tuple:
    """检查 cardforge 是否可用，返回 (可用, 提示)。"""
    if not os.path.isdir(CARDFORGE_DIR):
        return False, f"未找到 cardforge（期望 {CARDFORGE_DIR}，可在 settings.json 配 CARD_DIR）"
    if not os.path.isfile(os.path.join(CARDFORGE_DIR, "cardforge.py")):
        return False, f"cardforge 不完整（缺少 cardforge.py）：{CARDFORGE_DIR}"
    return True, ""


def _safe_join(base: str, rel: str) -> str:
    """把 rel 安全解析到 base 目录内，越界返回空串。"""
    base_r = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base_r, rel))
    if target != base_r and not target.startswith(base_r + os.sep):
        return ""
    return target


def material_path(cat: str, name: str) -> str:
    """在 cardforge assets/<子目录>/ 下找素材文件，返回绝对路径或 None（含防穿越）。"""
    sub = _CAT_DIRS.get(cat)
    if not sub or not name:
        return None
    d = os.path.join(CARDFORGE_DIR, "assets", sub)
    if not os.path.isdir(d):
        return None
    cands = [os.path.join(d, name)]
    if not name.lower().endswith(_IMG_EXT):
        cands.append(os.path.join(d, name + ".png"))
    for c in cands:
        p = _safe_join(d, os.path.basename(c))
        if p and os.path.isfile(p):
            return p
    return None


def list_materials(cat: str) -> list:
    """列出某类素材的文件名（用于素材包清单）。"""
    sub = _CAT_DIRS.get(cat)
    if not sub:
        return []
    d = os.path.join(CARDFORGE_DIR, "assets", sub)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(_IMG_EXT))


async def _run_forge(args: list, timeout: int = 1800):
    """执行 cardforge（JSON 输出），返回 (退出码, 输出文本)。

    跨平台解析解释器：
      1. 项目级共享 venv（tools/.venv，见 bot/core/venv.py）
      2. cardforge 自带的 venv（Windows 在 .venv/Scripts/，POSIX 在 .venv/bin/）
      3. 项目配置的 PYTHON（settings.json 的 PYTHON 字段）
      4. 当前解释器 sys.executable
    这样 Linux 上不会再退化成必须靠 cmd / cardforge.cmd 才能跑。

    ★ 为什么共享 venv 排在自带 venv 之前：共享 venv 是项目统一管理的，
      位置固定、可被多个工具复用；cardforge 自带的那个属于它自己的历史遗留，
      两者其实指向同一份依赖，优先用统一的那个以避免"两套环境行为不一致"。
    """
    import sys

    py = ""
    # 1) 项目级共享 venv
    try:
        from bot.core.venv import python_exe as _shared_py
        py = _shared_py(ROOT) or ""
    except Exception:
        py = ""

    # 2) cardforge 自带 venv
    if not py:
        for rel in (os.path.join(".venv", "Scripts", "python.exe"),
                    os.path.join(".venv", "bin", "python"),
                    os.path.join(".venv", "bin", "python3")):
            cand = os.path.join(CARDFORGE_DIR, rel)
            if os.path.isfile(cand):
                py = cand
                break

    # 3) settings.json 配置的 PYTHON
    if not py:
        try:
            from config import PYTHON as _CFG_PYTHON
            if _CFG_PYTHON and os.path.isfile(_CFG_PYTHON):
                py = _CFG_PYTHON
        except Exception:
            pass
    # 4) 当前解释器
    if not py:
        py = sys.executable or "python"

    script = os.path.join(CARDFORGE_DIR, "cardforge.py")
    if os.path.isfile(script):
        cmd = [py, script, *args]
    elif os.name == "nt":
        # Windows 且脚本缺失：回退 cardforge.cmd（首次会自动执行 setup.cmd）
        cmd = ["cmd", "/c", os.path.join(CARDFORGE_DIR, "cardforge.cmd"), *args]
    else:
        cmd = ["bash", os.path.join(CARDFORGE_DIR, "cardforge.sh"), *args]

    _log.info("执行 cardforge: %s", " ".join(cmd))
    # cardforge.py 输出中文 JSON 时 Windows 管道默认按 GBK 编码，
    # 强制子进程用 UTF-8 输出，否则 bot 端按 utf-8 解码会得到乱码路径
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 告诉 cardforge 用哪个 venv（它的 setup.sh/cardforge.sh 会读这个变量）。
    #   这样共享 venv 的位置由 bot 决定，cardforge 不必自己猜。
    try:
        from bot.core.venv import venv_dir as _venv_dir
        _vd = _venv_dir(ROOT)
        if _vd.is_dir():
            env["CARDFORGE_VENV"] = str(_vd)
    except Exception:
        pass
    # ★ 把 bot 的内置字体目录传给 cardforge。
    #   cardforge 的卡面文字原先硬编码 ImageFont.truetype("msyh.ttc")（Windows 专有），
    #   在 Linux 上会退化成没有 CJK 字形的 load_default()，卡面中文全是「口口口」。
    #   它现在会优先读这个环境变量指向的字体，这样两个项目共用同一份字体包，
    #   换设备/换服务器都不需要另外装字体。
    try:
        from bot.core.fonts import font_dir as _font_dir
        _fd = _font_dir(ROOT)
        if os.path.isdir(_fd):
            env["CARDFORGE_FONT_DIR"] = str(_fd)
    except Exception:
        pass
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=CARDFORGE_DIR, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 1, "cardforge 超时（首次运行需下载抠图模型，请耐心等待后重试）"
    try:
        text = (out or b"").decode("utf-8")
    except UnicodeDecodeError:
        text = (out or b"").decode("gbk", errors="replace")
    return proc.returncode, text


# ---------- 串行制作队列 ----------

_queue = asyncio.Queue(maxsize=20)
_worker_task = None


def ensure_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


async def submit(task: dict) -> tuple:
    """加入制作队列。返回 (是否入队, 提示文本)。"""
    if _queue.full():
        return False, "制作队列已满，请稍后再试喵"
    await _queue.put(task)
    ensure_worker()
    n = _queue.qsize() - 1
    if n > 0:
        return True, f"已加入制作队列，前方还有 {n} 张在等待喵"
    return True, "已进入制作队列，马上开始喵"


def has_queued(openid, name) -> bool:
    """制作队列（排队中/进行中）是否已有同名同用户任务，防并发重名。"""
    try:
        for t in list(_queue._queue):
            if t.get("openid") == openid and t.get("name") == name:
                return True
    except Exception:
        pass
    return False


async def _worker():
    while True:
        task = await _queue.get()
        try:
            from .commands import process_make_task
            await process_make_task(task)
        except Exception:
            _log.exception("卡牌制作任务异常")
        finally:
            _queue.task_done()


def copy_output(card_key: str, forge_dir: str) -> str:
    """把 cardforge 产物目录复制到 data/cards/assets/<cardKey>/（跳过 source.png），
    返回网页资产目录路径。"""
    from .carddata import _ASSETS_DIR
    dst = os.path.join(_ASSETS_DIR, card_key)
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(forge_dir, dst, ignore=shutil.ignore_patterns("source.png"))
    return dst
