# -*- coding: utf-8 -*-
"""项目级共享 venv：给 bot 里需要「独立依赖环境」的工具复用。

### 为什么要共享而不是每个工具各建一个

目前只有 `tools/cardforge` 需要独立 venv（它要 rembg + onnxruntime，
和 bot 主进程的依赖差别大、体积也大，不适合塞进主环境）。
但 venv 是个通用能力 —— 以后可能还有别的工具需要隔离环境。
所以放在**项目级的固定位置**，由工具**指向**它，而不是各家自建：

    tools/.venv/            ← 唯一位置（跨平台）
      Windows: Scripts/python.exe
      POSIX  : bin/python

好处：
- 只装一次依赖，多个工具共享（重复的 rembg/onnxruntime 动辄几百 MB）
- 卸载/重建只针对一个目录，不会在各工具目录里散落
- 位置固定，部署脚本与排查都只认这一个路径

### 与工具的关系

工具有两种接入方式，**互相兼容**：
1. 工具自己支持配置 venv 路径（如 cardforge 的 `settings.json` 的 `venv` 字段）
   —— 由 `bot` 把本模块解析出的路径写进去；
2. 工具没有该能力 —— 由调用方直接用 `python_exe()` 拿解释器路径去跑。

`tools/.venv` 已在 `.gitignore` 里（`.venv/` 规则覆盖），不入库。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 共享 venv 相对项目根的位置
VENV_PARTS = ("tools", ".venv")

# 跨平台的解释器相对路径（Windows 在前，POSIX 在后）
_PY_RELPATHS = (
    ("Scripts", "python.exe"),
    ("bin", "python"),
    ("bin", "python3"),
)


def venv_dir(project_root: str | os.PathLike | None = None) -> Path:
    """返回共享 venv 的绝对路径（不保证存在）。"""
    if project_root is None:
        # 本文件在 bot/core/ 下，往上两级到项目根
        project_root = Path(__file__).resolve().parent.parent.parent
    return Path(project_root).joinpath(*VENV_PARTS)


def python_exe(project_root: str | os.PathLike | None = None) -> str | None:
    """返回共享 venv 里的解释器路径；不存在则返回 None。"""
    d = venv_dir(project_root)
    for parts in _PY_RELPATHS:
        p = d.joinpath(*parts)
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return None


def exists(project_root: str | os.PathLike | None = None) -> bool:
    """共享 venv 是否已就绪。"""
    return python_exe(project_root) is not None


def create(project_root: str | os.PathLike | None = None,
           base_python: str | None = None,
           verbose: bool = False) -> tuple[bool, str]:
    """创建共享 venv（已存在则跳过）。返回 (是否成功, 说明)。

    base_python 为空时用当前解释器 sys.executable。
    """
    def log(m):
        if verbose:
            print("[venv] %s" % m, flush=True)

    d = venv_dir(project_root)
    existing = python_exe(project_root)
    if existing:
        return True, "已存在：%s" % existing

    base = base_python or sys.executable
    if not base:
        return False, "找不到可用的基础 Python 解释器"

    d.parent.mkdir(parents=True, exist_ok=True)
    log("创建 %s（基础解释器 %s）" % (d, base))
    try:
        r = subprocess.run([base, "-m", "venv", str(d)],
                           capture_output=True, text=False, timeout=600)
    except Exception as e:
        return False, "创建 venv 失败：%s: %s" % (type(e).__name__, e)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        if "ensurepip" in err or "No module named venv" in err:
            err += "\n（系统缺少 python3-venv，Debian/Ubuntu 需 apt install python3-venv）"
        return False, "创建 venv 失败：%s" % (err or "退出码 %d" % r.returncode)

    ex = python_exe(project_root)
    if not ex:
        return False, "venv 已创建但找不到解释器：%s" % d
    log("完成：%s" % ex)
    return True, "已创建：%s" % ex


def install_requirements(req_file: str | os.PathLike,
                         project_root: str | os.PathLike | None = None,
                         upgrade_pip: bool = True,
                         index_url: str | None = None,
                         verbose: bool = False) -> tuple[bool, str]:
    """把依赖装进共享 venv。返回 (是否成功, 输出尾部)。

    ★ 调用方注意：官方 PyPI 在部分网络环境下极慢，index_url 可指定镜像源。
      但**不要把镜像源写死进代码** —— 不同部署环境可用源不同，
      由 settings.json / install 脚本决定更灵活。

    ★ 默认带 --no-cache-dir：某些受限环境（含安全钩子的沙箱）会在 pip
      清理 HTTP 缓存时拦截文件删除，导致整个安装以 SystemExit 中断 ——
      报错栈看起来像 pip 内部崩溃，其实是环境拦截。关掉缓存可绕开。
    """
    ex = python_exe(project_root)
    if not ex:
        return False, "共享 venv 不存在，请先调用 create()"
    if not Path(req_file).is_file():
        return False, "依赖文件不存在：%s" % req_file

    def run(args):
        cmd = [ex, "-m", "pip"] + args
        if index_url:
            cmd += ["-i", index_url]
        return subprocess.run(cmd, capture_output=True, text=False, timeout=3600)

    if upgrade_pip:
        try:
            run(["install", "--upgrade", "pip", "-q", "--no-cache-dir"])
        except Exception:
            pass  # pip 升级失败不该阻断依赖安装

    try:
        r = run(["install", "-r", str(req_file), "--no-cache-dir"])
    except Exception as e:
        return False, "安装依赖异常：%s: %s" % (type(e).__name__, e)

    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    tail = "\n".join((out + err).strip().splitlines()[-25:])
    if verbose:
        print("[venv] pip 输出尾部：\n%s" % tail, flush=True)
    return r.returncode == 0, tail


def describe(project_root: str | os.PathLike | None = None) -> str:
    """人类可读的状态描述（用于 WebUI / 运维命令）。"""
    d = venv_dir(project_root)
    ex = python_exe(project_root)
    if not ex:
        return "未创建（%s）" % d
    try:
        r = subprocess.run([ex, "--version"], capture_output=True, text=False, timeout=30)
        ver = (r.stdout or r.stderr or b"").decode("utf-8", "replace").strip()
    except Exception:
        ver = "版本未知"
    return "%s（%s）" % (ex, ver)
