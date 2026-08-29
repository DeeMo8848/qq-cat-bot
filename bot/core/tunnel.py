# -*- coding: utf-8 -*-
"""cloudflared 内网穿透管理：自动启动隧道、解析公网回调地址。"""

import os
import re
import subprocess
import threading
import time

from config import ROOT

_BASE_DIR = ROOT
_CFD_EXE = os.path.join(_BASE_DIR, "cloudflared.exe")
_LOG_FILE = os.path.join(_BASE_DIR, "cloudflared.log")

# 具名隧道固定信息：隧道名、配置文件、稳定公网地址（不再用随机 trycloudflare）
_TUNNEL_NAME = "qqbot"
_TUNNEL_CONFIG = os.path.join(_BASE_DIR, "tunnel", "config.yml")
_TUNNEL_URL = "https://qqbot.deemo8848.dpdns.org"

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# 模块级单例，方便 sender 等模块随时取当前公网地址
_manager = None


def set_manager(manager):
    global _manager
    _manager = manager


def get_url():
    """返回当前 cloudflared 公网地址（未启动或尚未解析到时为 None）。"""
    return _manager.get_url() if _manager else None


class TunnelManager:
    def __init__(self, local_port):
        self.local_port = local_port
        self.proc = None
        self.url = None
        self._lock = threading.Lock()
        set_manager(self)

    def start(self):
        """若隧道未运行则启动具名隧道，并直接使用固定公网地址。"""
        if self._is_running():
            self.url = _TUNNEL_URL
            return
        if not os.path.exists(_CFD_EXE):
            print("[隧道] 未找到 cloudflared.exe，跳过内网穿透（回调地址不可用）")
            return
        log = open(_LOG_FILE, "a", encoding="utf-8", errors="replace")
        # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP：让 cloudflared 脱离机器人进程
        # 的进程组独立运行，这样重启机器人时不会被连带杀掉、地址保持不变。
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            [_CFD_EXE, "tunnel", "--config", _TUNNEL_CONFIG, "run", _TUNNEL_NAME],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
        self.url = _TUNNEL_URL
        print(f"[隧道] cloudflared 具名隧道已启动，公网地址: {_TUNNEL_URL}（日志: {_LOG_FILE}）")
        threading.Thread(target=self._watch_log, daemon=True).start()

    def _is_running(self):
        # 用字节模式采集，避免中文 Windows 上 tasklist 的 GBK 输出被当 utf-8 解码而在后台线程抛 UnicodeDecodeError
        try:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                capture_output=True, timeout=10,
            )
            return b"cloudflared.exe" in res.stdout
        except Exception:
            return False

    def _load_existing_url(self):
        self.url = _TUNNEL_URL

    def _watch_log(self):
        last_size = 0
        while True:
            try:
                if os.path.exists(_LOG_FILE):
                    size = os.path.getsize(_LOG_FILE)
                    if size > last_size:
                        with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_size)
                            remaining = f.read()
                            if "Registered tunnel connection" in remaining or "INF" in remaining:
                                pass  # 连接建立信息留作日志；稳定地址固定为 _TUNNEL_URL
                        last_size = size
            except Exception:
                pass
            time.sleep(2)

    def get_url(self):
        with self._lock:
            return self.url

    def is_running(self):
        return self._is_running() or (self.proc is not None and self.proc.poll() is None)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
