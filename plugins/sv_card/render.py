# -*- coding: utf-8 -*-
"""影之诗制卡器 bot 插件 —— 渲染调用层。

通过 `render_bridge.mjs` 子进程调用原项目的 wasm 内核渲染卡图。
**独立进程**的好处：字体/内存用完即释放，渲染崩溃也不会波及 bot 主进程。
实测单次渲染约 0.3~0.6 秒（含 Node 启动）。

资源与桥脚本都在插件目录内，不依赖原项目路径，服务器可独立部署。
"""

import asyncio
import json
import os
import shutil
import sys
import uuid

from config import ROOT

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(PLUGIN_DIR, "assets")
BRIDGE = os.path.join(PLUGIN_DIR, "render_bridge.mjs")

TMP_DIR = os.path.join(ROOT, "tmp", "sv_card")

# Node 可执行文件探测顺序：settings.json 覆盖 → PATH → 常见安装路径
_NODE_FALLBACKS = [
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
    "/usr/bin/node",
    "/usr/local/bin/node",
    "/usr/local/node/bin/node",
]


def _cfg_node() -> str:
    """settings.json 里可选的 NODE_EXE 覆盖。"""
    try:
        from config import _CFG  # type: ignore[attr-defined]
        v = _CFG.get("NODE_EXE")
        if v:
            return str(v)
    except Exception:
        pass
    return ""


def resolve_node() -> str:
    """定位 node 可执行文件；找不到返回空串。"""
    override = _cfg_node()
    if override and os.path.isfile(override):
        return override
    w = shutil.which("node")
    if w and os.path.isfile(w):
        return w
    for p in _NODE_FALLBACKS:
        if os.path.isfile(p):
            return p
    return ""


def assets_ready() -> bool:
    """渲染资源是否就位（未就位时给用户明确提示，而不是抛栈）。"""
    need = [
        os.path.join(ASSETS_DIR, "pkg", "wbmaker.js"),
        os.path.join(ASSETS_DIR, "pkg", "wbmaker_bg.wasm"),
        os.path.join(ASSETS_DIR, "fonts", "arweibeigbpro_bd.otf"),
        os.path.join(ASSETS_DIR, "fonts", "FOT-TsukuAOldMin-Pr6-E.digits.otf"),
    ]
    return all(os.path.isfile(p) for p in need)


def _tmp_path(suffix: str) -> str:
    os.makedirs(TMP_DIR, exist_ok=True)
    return os.path.join(TMP_DIR, uuid.uuid4().hex + suffix)


async def render(config: dict, style: str = "wb", art_path: str = None,
                 timeout: int = 120) -> str:
    """渲染卡图，返回生成的 PNG 绝对路径。

    config —— 已组装好的 CardConfig 字典
    style  —— "wb"（单卡图）/ "diy"（效果图）
    art_path —— 立绘 PNG 路径（可为 None）
    """
    node = resolve_node()
    if not node:
        raise RuntimeError("未找到 node 可执行文件（可在 settings.json 配 NODE_EXE）")
    if not assets_ready():
        raise RuntimeError("渲染资源缺失：plugins/sv_card/assets 未就位")
    if not os.path.isfile(BRIDGE):
        raise RuntimeError("渲染桥缺失：%s" % BRIDGE)

    req_path = _tmp_path(".json")
    out_path = _tmp_path(".png")
    req = {
        "assets": ASSETS_DIR,
        "config": config,
        "art": art_path,
        "out": out_path,
        "style": style,
    }
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False)

    try:
        proc = await asyncio.create_subprocess_exec(
            node, BRIDGE, req_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("渲染超时（%ss）" % timeout)

        if proc.returncode != 0:
            msg = (stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("渲染失败：%s" % (msg[-400:] or "未知错误"))

        if not os.path.isfile(out_path):
            raise RuntimeError("渲染未产出文件")

        info = {}
        try:
            info = json.loads((stdout or b"{}").decode("utf-8", "replace").strip())
        except Exception:
            pass
        if info.get("totalMs") is not None:
            print("[sv_card] 渲染完成 %.0fms (字体 %.0fms) -> %s"
                  % (info.get("totalMs", 0), info.get("fontMs", 0), out_path), flush=True)
        return out_path
    finally:
        try:
            os.remove(req_path)
        except Exception:
            pass


def cleanup(*paths):
    """删除临时文件（渲染产物发出去之后调用）。"""
    for p in paths:
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass
