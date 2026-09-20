# -*- coding: utf-8 -*-
"""跨平台适配层（Windows / Linux 通用）。

把「同一件事在不同系统上的不同做法」集中到本模块，其余代码只调这里的函数，
避免平台判断散落各处。当前覆盖：

    · 可执行文件后缀（.exe / 无）
    · 外部工具解析（项目 tools/ → settings 指定 → 系统 PATH）
    · 子进程「脱离父进程独立运行」的启动方式
    · 进程存活 / 按名查进程
    · 中文字体路径
    · 进程退出与重启（execv 自替换 / 交守护进程）
    · 环境变量与用户目录

全部函数在两种系统上都可调用，不含仅某平台可用的 import。
"""

import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------- 平台标识

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# 可执行文件后缀：Windows 需要 .exe，类 Unix 为空
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

# 新进程组标志（脱离父进程）
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008


def exe_name(name: str) -> str:
    """给裸名补上当前平台的可执行后缀：'ffmpeg' -> 'ffmpeg.exe' / 'ffmpeg'。"""
    name = (name or "").strip()
    if not name:
        return name
    if name.lower().endswith(".exe"):
        return name
    return name + EXE_SUFFIX


# ---------------------------------------------------------------- 路径

def norm_path(p: str) -> str:
    """规范化路径分隔符（Windows 上把 / 转成 \\，其他平台不动）。"""
    if not p:
        return p
    return os.path.normpath(p)


def app_base_dir() -> str:
    """用户级配置目录（放代理客户端配置等）：Windows 用 APPDATA，Linux 用 ~/.config。"""
    if IS_WINDOWS:
        return os.environ.get("APPDATA", "") or os.path.expanduser("~")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


def local_base_dir() -> str:
    """用户级本地数据目录：Windows 用 LOCALAPPDATA，Linux 用 ~/.local/share。"""
    if IS_WINDOWS:
        return os.environ.get("LOCALAPPDATA", "") or os.path.expanduser("~")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def find_font(*names: str) -> str | None:
    """按候选文件名在常见字体目录里找字体，返回首个存在者。

    调用方可传入平台相关候选（如 Windows 的 msyh.ttc、Linux 的 noto 字体），
    本函数只负责「在哪些目录里找」。
    """
    dirs = []
    if IS_WINDOWS:
        dirs += [
            os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        ]
    else:
        dirs += [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.join(os.path.expanduser("~"), ".fonts"),
            os.path.join(os.path.expanduser("~"), ".local", "share", "fonts"),
            "/System/Library/Fonts",
            "/Library/Fonts",
        ]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
        # 字体常放在子目录里（如 /usr/share/fonts/truetype/noto/），递归找一次
        try:
            for root, _sub, files in os.walk(d):
                for n in names:
                    if n in files:
                        return os.path.join(root, n)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- 进程

def popen_kwargs_detached() -> dict:
    """让子进程脱离当前进程组独立运行的 Popen 参数。

    Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    Linux:   start_new_session=True（等价 setsid）
    目的：重启 bot 时不会连带杀掉 cloudflared 等长驻子进程。
    """
    if IS_WINDOWS:
        return {
            "creationflags": _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            "close_fds": True,
        }
    return {"start_new_session": True, "close_fds": True}


def popen_kwargs_silent() -> dict:
    """静默启动子进程、不弹控制台窗口的参数（Windows 专用意义，Linux 忽略）。"""
    if IS_WINDOWS:
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {}


def process_running(name: str) -> bool:
    """按进程名（不含后缀，如 'cloudflared'）判断进程是否在运行。

    不用 psutil，只用系统自带命令，减少依赖：
      Windows: tasklist
      Linux:   pgrep
    """
    bare = name[:-4] if name.lower().endswith(".exe") else name
    try:
        if IS_WINDOWS:
            res = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {bare}.exe"],
                capture_output=True, timeout=10,
            )
            return (bare + ".exe").encode() in res.stdout
        res = subprocess.run(
            ["pgrep", "-x", bare], capture_output=True, timeout=10,
        )
        return res.returncode == 0
    except Exception:
        return False


def kill_process(name: str, force: bool = False) -> bool:
    """按进程名结束进程：Windows taskkill，Linux pkill。"""
    bare = name[:-4] if name.lower().endswith(".exe") else name
    try:
        if IS_WINDOWS:
            cmd = ["taskkill", "/IM", bare + ".exe"]
            if force:
                cmd.append("/F")
            subprocess.run(cmd, capture_output=True, timeout=10)
        else:
            cmd = ["pkill", "-f" if force else "-x", bare]
            subprocess.run(cmd, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def terminate_tree(proc, timeout: float = 5.0) -> None:
    """尽力结束一个子进程（含其子进程），失败则强杀。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


# ---------------------------------------------------------------- 重启

def can_exec_self() -> bool:
    """当前平台是否支持用 execv 原地替换进程（Linux 支持，Windows 不支持）。"""
    return not IS_WINDOWS and hasattr(os, "execv")


def exec_self(python_exe: str, script: str, cwd: str) -> None:
    """用新进程原地替换当前进程（不改变 PID，便于被 systemd/supervisor 管理）。

    仅在 Linux/macOS 可用；Windows 会抛异常，调用方需先用 can_exec_self() 判断。
    """
    os.chdir(cwd)
    os.execv(python_exe, [python_exe, script])


def restart_process(python_exe: str, script: str, cwd: str) -> None:
    """重启当前程序：能 execv 就原地替换，否则退出由外部守护/服务拉起。"""
    if can_exec_self():
        exec_self(python_exe, script, cwd)
    # Windows 无 execv：直接退出，交给启动脚本（启动bot.bat 循环）或人工重启
    os._exit(0)


# ---------------------------------------------------------------- 环境自检

def describe() -> dict:
    """返回当前平台信息，供 Web 后台 / 启动日志展示。"""
    return {
        "os": "Windows" if IS_WINDOWS else ("Linux" if IS_LINUX else ("macOS" if IS_MACOS else sys.platform)),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "exe_suffix": EXE_SUFFIX or "(无)",
        "exec_self": can_exec_self(),
        "cwd": os.getcwd(),
    }
