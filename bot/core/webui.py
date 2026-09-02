# -*- coding: utf-8 -*-
"""本地 Web 后台：查看机器人状态、管理功能开关、查看日志。

访问方式：浏览器打开 http://127.0.0.1:8080
"""

import asyncio
import json
import os
import re
import time

from aiohttp import web

from bot import commands
from plugins import randomimg
from bot.core import state
from bot.ai import ai as ai_mod
from config import WEBUI_PORT, WHITELIST_IPS, ROOT

# 公网 IP 查询源（按顺序尝试）
_IP_PROVIDERS = [
    "https://api.ip.sb/ip",
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
]

_LOG_FILE = os.path.join(ROOT, "botpy.log")


def _module_groups():
    """返回 Web 后台「游戏 / 其他」两层功能树及其命令集合。

    - GAME_GROUPS: [(key, 名, [命令名])]，游戏娱乐下每个插件整体一个开关
    - GAME_CMD_NAMES: 全部游戏命令（用于隐藏与一键开关）
    - OTHER_PLUGINS: [(key, 名, [命令名], sub)]，其他功能下每个插件整体一个开关；
      sub 供插件内还有独立功能的嵌套开关（仅搜图），格式同 GAME_GROUPS
    - OTHER_CMD_NAMES: 全部其他功能命令（用于隐藏与一键开关）
    """
    from plugins.games import GAME_CMD_NAMES, GAME_GROUPS
    from plugins.fishing import FISHING_CMD_NAMES   # 钓鱼并入「游戏娱乐」
    from plugins.searchimg import SEARCH_GROUPS, SEARCH_CMD_NAMES
    from plugins.drift import DRIFT_CMD_NAMES
    from plugins.eat import EAT_CMD_NAMES
    from plugins.mcskin import MCSKIN_CMD_NAMES
    from plugins.mirage import MIRAGE_CMD_NAMES
    from plugins.emojimix import EMOJIMIX_CMD_NAMES
    from plugins.netease_music import NCM_CMD_NAMES
    from plugins.jrys import JRESY_CMD_NAMES
    from plugins.words import WORD_CMD_NAMES

    other_plugins = [
        ("search_img", "搜图", SEARCH_CMD_NAMES, SEARCH_GROUPS),
        ("drift_bottle", "漂流瓶", DRIFT_CMD_NAMES, []),
        ("eat_food", "吃什么", EAT_CMD_NAMES, []),
        ("mc_skin", "MC皮肤", MCSKIN_CMD_NAMES, []),
        ("mirage", "幻影坦克", MIRAGE_CMD_NAMES, []),
        ("emoji_mix", "emojimix", EMOJIMIX_CMD_NAMES, []),
        ("netease_music", "网易云点歌", NCM_CMD_NAMES, []),
        ("jrys", "今日运势签到", JRESY_CMD_NAMES, []),
        ("random_words", "随机一言/名言/诗词", WORD_CMD_NAMES, []),
    ]
    other_names = {n for _, _, ns, _ in other_plugins for n in ns}
    # 「其他功能」子菜单入口命令（cmd_other_menu）并入该模块，避免与模块卡片重复显示
    other_names = other_names | {"cmd_other_menu"}
    # 钓鱼插件归入「游戏娱乐」模块（在 web 层合并，避免 import 环）
    game_groups = list(GAME_GROUPS) + [("fishing", "钓鱼", sorted(FISHING_CMD_NAMES))]
    game_names = set(GAME_CMD_NAMES) | FISHING_CMD_NAMES
    return game_groups, game_names, other_plugins, other_names


def _plugin_switch(key, title, names, sub):
    """构造一个插件开关（含可选嵌套子开关）。"""
    return {
        "name": key,
        "title": title,
        "help": "",
        "enabled": any(state.is_enabled(x) for x in names),
        "sub": [
            {"name": k, "title": t, "help": "",
             "enabled": any(state.is_enabled(x) for x in ns)}
            for k, t, ns in sub
        ],
    }


class WebUI:
    def __init__(self, bot, tunnel=None):
        self.bot = bot
        self.tunnel = tunnel
        self.app = web.Application()
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/api/status", self.status)
        self.app.router.add_post("/api/toggle", self.toggle)
        self.app.router.add_post("/api/group_rule", self.set_group_rule)
        self.app.router.add_get("/api/bilibili/mode", self.bilibili_mode)
        self.app.router.add_post("/api/bilibili/mode", self.set_bilibili_mode)
        self.app.router.add_get("/api/parse/platforms", self.parse_platforms)
        self.app.router.add_post("/api/parse/platforms", self.set_parse_platform)
        # Lolicon 过滤开关
        self.app.router.add_get("/api/lolicon/filters", self.lolicon_filters)
        self.app.router.add_post("/api/lolicon/filters", self.set_lolicon_filter)
        self.app.router.add_post("/api/shutdown", self.shutdown)
        # AI 对话配置
        self.app.router.add_get("/api/ai/config", self.ai_config)
        self.app.router.add_post("/api/ai/config", self.ai_save_config)
        self.app.router.add_post("/api/ai/test", self.ai_test)
        self.app.router.add_post("/api/ai/models", self.ai_models)
        self.app.router.add_get("/api/ai/balance", self.ai_balance)
        self.app.router.add_get("/api/ai/memory", self.ai_memory)
        self.app.router.add_post("/api/ai/memory/delete", self.ai_memory_delete)
        # 随机一图预览代理（供独立预览网页按 source 取一张图）
        self.app.router.add_get("/api/randomimg/preview", self.randomimg_preview)
        self._ip = None
        self._ip_time = 0.0

    # ---------- 页面 ----------
    async def index(self, request):
        resp = web.Response(text=PAGE_HTML, content_type="text/html", charset="utf-8")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    # ---------- API ----------
    async def status(self, request):
        ip = await self._current_ip()
        ip_ok = ip in WHITELIST_IPS
        rand_names = randomimg.RANDOMIMG_CMD_NAMES
        (GAME_GROUPS, GAME_CMD_NAMES, OTHER_PLUGINS, OTHER_CMD_NAMES) = _module_groups()
        rand_cmds = [f for f in commands._COMMANDS if f.__name__ in rand_names]
        # 属于各模块/插件的底层命令不留独立开关，统一归为插件的总开关
        hidden = rand_names | GAME_CMD_NAMES | OTHER_CMD_NAMES
        commands_list = [
            {
                "name": func.__name__,
                "keywords": func.keywords,
                "help": func.help,
                "enabled": state.is_enabled(func.__name__),
                "group_rule": state.get_group_rule(func.__name__),
            }
            for func in commands._COMMANDS
            if func.__name__ not in hidden
        ]
        commands_list.append({
            "name": "parse_enabled",
            "title": "多平台解析",
            "keywords": [],
            "help": "多平台解析（B站卡片/抖音/快手/A站/网易云）",
            "enabled": state.is_enabled("parse_enabled"),
            "group_rule": None,
        })
        commands_list.append({
            "name": "cmd_randomimg",
            "title": "随机图片",
            "keywords": [],
            "help": "随机图片（UAPI/樱花/栗次元/兽耳酱/天逸/小姐姐/南风/Yuki/Lolicon/龙图/猪猪）",
            "enabled": any(state.is_enabled(f.__name__) for f in rand_cmds),
            "group_rule": None,
            "sub": [
                {
                    "name": f.__name__,
                    "title": (f.keywords[0] if f.keywords else f.__name__),
                    "help": f.help or "",
                    "enabled": state.is_enabled(f.__name__),
                }
                for f in rand_cmds
            ],
        })
        commands_list.append({
            "name": "cmd_game",
            "title": "游戏娱乐",
            "keywords": [],
            "help": "游戏娱乐（吉星派对 / 21点 / 海龟汤）",
            "enabled": any(state.is_enabled(n) for n in GAME_CMD_NAMES),
            "group_rule": None,
            "sub": [_plugin_switch(k, t, ns, []) for k, t, ns in GAME_GROUPS],
        })
        # 「其他功能」插件树：每个插件一个总开关；搜图插件内含搜番/搜角色/搜出处子开关
        commands_list.append({
            "name": "cmd_other",
            "title": "其他功能",
            "keywords": [],
            "help": "其他功能（搜图 / 漂流瓶 / 吃什么 / MC皮肤 / 幻影坦克 / emojimix / 网易云点歌 / 今日运势 / 随机文案）",
            "enabled": any(state.is_enabled(n) for n in OTHER_CMD_NAMES),
            "group_rule": None,
            "sub": [_plugin_switch(k, t, ns, sub) for k, t, ns, sub in OTHER_PLUGINS],
        })
        robot = getattr(self.bot, "robot", None)
        tunnel_url = self.tunnel.get_url() if self.tunnel else None
        tunnel_running = self.tunnel.is_running() if self.tunnel else False
        return web.json_response({
            "online": bool(getattr(self.bot, "online", False)),
            "bot_name": getattr(robot, "name", "-"),
            "bot_id": getattr(robot, "id", "-"),
            "last_ready": getattr(self.bot, "last_ready", None),
            "ip": ip,
            "ip_ok": ip_ok,
            "whitelist_ips": WHITELIST_IPS,
            "tunnel_url": tunnel_url,
            "tunnel_running": tunnel_running,
            "commands": commands_list,
            "recent_groups": state.get_recent_groups(),
            "bilibili_mode": state.get_bilibili_mode(),
            "lolicon_filters": state.get_lolicon_filters(),
            "log_tail": self._log_tail(60),
        })

    async def toggle(self, request):
        data = await request.json()
        name = data.get("name", "")
        enabled = bool(data.get("enabled"))
        (GAME_GROUPS, GAME_CMD_NAMES, OTHER_PLUGINS, OTHER_CMD_NAMES) = _module_groups()
        # 插件/功能组开关 key -> 该开关下所有命令名（含搜图插件的子开关）
        group_map = {}
        for key, _title, names, sub in OTHER_PLUGINS:
            group_map[key] = set(names)
            for k, _t, ns in sub:
                group_map[k] = set(ns)
        for key, _title, names in GAME_GROUPS:
            group_map[key] = set(names)
        known = (
            {f.__name__ for f in commands._COMMANDS}
            | {"parse_enabled", "cmd_randomimg", "cmd_game", "cmd_other"}
            | set(group_map)
        )
        if name not in known:
            return web.json_response({"ok": False, "msg": "命令不存在"}, status=400)
        if name in group_map:
            # 插件/功能组开关（如 emojimix / 21点 / 搜番）：一键开/关该插件下所有命令
            for n in group_map[name]:
                state.set_enabled(n, enabled)
        elif name == "cmd_randomimg":
            # 「随机图片」模块总开关：一键开/关所有随机图子命令
            for f in commands._COMMANDS:
                if f.__name__ in randomimg.RANDOMIMG_CMD_NAMES:
                    state.set_enabled(f.__name__, enabled)
        elif name == "cmd_game":
            # 「游戏娱乐」模块总开关：一键开/关所有游戏子命令
            for n in GAME_CMD_NAMES:
                state.set_enabled(n, enabled)
        elif name == "cmd_other":
            # 「其他功能」模块总开关：一键开/关其余所有功能命令
            for n in OTHER_CMD_NAMES:
                state.set_enabled(n, enabled)
        else:
            state.set_enabled(name, enabled)
        return web.json_response({"ok": True, "name": name, "enabled": enabled})

    async def set_group_rule(self, request):
        data = await request.json()
        name = data.get("name", "")
        if not any(f.__name__ == name for f in commands._COMMANDS):
            return web.json_response({"ok": False, "msg": "命令不存在"}, status=400)
        mode = (data.get("mode") or "").strip()
        groups = data.get("groups") or []
        if isinstance(groups, str):
            groups = [g for g in re.split(r"[,，\s]+", groups) if g]
        state.set_group_rule(name, mode, groups)
        return web.json_response({"ok": True, "name": name, "mode": mode, "groups": groups})

    async def bilibili_mode(self, request):
        return web.json_response({"ok": True, "mode": state.get_bilibili_mode()})

    async def set_bilibili_mode(self, request):
        data = await request.json() or {}
        mode = state.set_bilibili_mode(data.get("mode", ""))
        return web.json_response({"ok": True, "mode": mode})

    async def parse_platforms(self, request):
        from bot.parse import gateway as pg
        return web.json_response({"ok": True, "platforms": pg.list_parse_platforms()})

    async def set_parse_platform(self, request):
        data = await request.json() or {}
        name = data.get("name", "")
        enabled = bool(data.get("enabled"))
        from bot.parse import gateway as pg
        if not pg.set_platform_enabled(name, enabled):
            return web.json_response({"ok": False, "msg": "平台不存在"}, status=400)
        await pg.reload_platforms()
        return web.json_response({"ok": True, "name": name, "enabled": enabled})

    async def lolicon_filters(self, request):
        """返回 Lolicon 过滤开关状态，如 {"nsfw": true, "racy": true}。"""
        return web.json_response({"ok": True, "filters": state.get_lolicon_filters()})

    async def set_lolicon_filter(self, request):
        data = await request.json() or {}
        name = (data.get("name") or "").strip()
        enabled = bool(data.get("enabled"))
        if name not in ("nsfw", "racy"):
            return web.json_response({"ok": False, "msg": "过滤项不存在"}, status=400)
        state.set_lolicon_filter(name, enabled)
        return web.json_response({"ok": True, "name": name, "enabled": enabled})

    # ---------- AI 对话配置 API ----------
    async def ai_config(self, request):
        return web.json_response(await ai_mod.get_config())

    async def ai_save_config(self, request):
        data = await request.json() or {}
        cfg = await ai_mod.save_config(data)
        return web.json_response({"ok": True, "config": cfg})

    async def ai_test(self, request):
        data = await request.json() or {}
        try:
            reply = await ai_mod.test_ping(data.get("message") or "你好，在吗喵")
            return web.json_response({"ok": True, "reply": reply})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)})

    async def ai_models(self, request):
        try:
            models = await ai_mod.fetch_models()
            return web.json_response({"ok": True, "models": models})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e)})

    async def ai_balance(self, request):
        try:
            bal = await ai_mod.fetch_balance()
        except Exception:
            bal = None
        return web.json_response({"ok": True, "balance": bal})

    async def ai_memory(self, request):
        mem = await ai_mod.all_memory()
        return web.json_response({"ok": True, "memory": mem})

    async def ai_memory_delete(self, request):
        data = await request.json() or {}
        oid = data.get("openid", "")
        if oid:
            await ai_mod.delete_memory(oid)
        return web.json_response({"ok": True})

    async def randomimg_preview(self, request):
        """随机一图预览代理：按 source 从对应 API/本地目录取一张图返回字节。

        供独立预览网页（如「qqbot - 副本」里的预览工具）通过 <img> 直接展示，
        从而绕开浏览器跨域限制。失败返回 502 + 文案，网页端显示「api死了喵」。
        """
        source = request.query.get("source", "")
        if not source:
            return web.Response(status=400, text="缺少 source 参数")
        q = {k: v for k, v in request.query.items()}
        data, ctype = await randomimg.fetch_preview_image(source, **q)
        if not data:
            return web.Response(status=502, text="api死了喵")
        return web.Response(body=data, content_type=ctype or "image/jpeg")

    async def shutdown(self, request):
        """关闭机器人并退出程序（优雅关闭 + 兜底强制退出）。"""
        try:
            await self.bot.close()
        except Exception:
            pass
        # 给清理留一点时间，然后强制退出进程
        asyncio.get_event_loop().call_later(1.0, os._exit, 0)
        return web.json_response({"ok": True, "msg": "正在关闭机器人…"})

    # ---------- 内部工具 ----------
    async def _current_ip(self):
        """获取当前公网出口 IP（带 60 秒缓存）。"""
        if self._ip and time.time() - self._ip_time < 60:
            return self._ip
        import aiohttp
        async with aiohttp.ClientSession() as session:
            for url in _IP_PROVIDERS:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        text = (await resp.text()).strip()
                        if text and text[0].isdigit():
                            self._ip = text
                            self._ip_time = time.time()
                            return text
                except Exception:
                    continue
        return self._ip or "获取失败"

    def _log_tail(self, n):
        if not os.path.exists(_LOG_FILE):
            return []
        try:
            with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return lines[-n:]
        except Exception:
            return []


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 机器人后台</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:"Microsoft YaHei",system-ui,sans-serif; background:#f0f2f5; color:#333; padding:24px; }
  .wrap { max-width:900px; margin:0 auto; }
  h1 { font-size:22px; margin-bottom:4px; }
  .sub { color:#888; font-size:13px; margin-bottom:20px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin-bottom:20px; }
  .card { background:#fff; border-radius:12px; padding:18px; box-shadow:0 1px 4px rgba(0,0,0,.06); }
  .card h2 { font-size:15px; color:#2563eb; font-weight:700; margin-bottom:12px; }
  .big { font-size:26px; font-weight:700; }
  .badge { display:inline-block; padding:3px 12px; border-radius:20px; font-size:13px; color:#fff; }
  .badge.on { background:#22c55e; }
  .badge.off { background:#ef4444; }
  .badge.warn { background:#f59e0b; }
  .row { display:flex; justify-content:space-between; align-items:center; padding:10px 0; border-bottom:1px solid #f1f1f1; }
  .row:last-child { border-bottom:none; }
  .kws { color:#2563eb; font-weight:600; }
  .help { color:#888; font-size:12px; }
  .switch { position:relative; width:44px; height:24px; flex-shrink:0; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:#cbd5e1; border-radius:24px; cursor:pointer; transition:.2s; }
  .slider:before { content:""; position:absolute; width:18px; height:18px; left:3px; top:3px; background:#fff; border-radius:50%; transition:.2s; }
  .switch input:checked + .slider { background:#22c55e; }
  .switch input:checked + .slider:before { transform:translateX(20px); }
  .log { background:#0f172a; color:#a5f3fc; border-radius:10px; padding:14px; font-family:Consolas,monospace; font-size:12px; line-height:1.6; max-height:260px; overflow:auto; white-space:pre-wrap; word-break:break-all; }
  .ip-ok { color:#22c55e; font-weight:600; }
  .ip-bad { color:#ef4444; font-weight:600; }
  .tip { font-size:12px; color:#888; margin-top:8px; }
  .grp { display:flex; gap:8px; padding:2px 0 10px; align-items:center; }
  .grp select { padding:5px 8px; border-radius:6px; border:1px solid #d1d5db; font-size:12px; background:#fff; }
  .grp input { flex:1; padding:5px 8px; border-radius:6px; border:1px solid #d1d5db; font-size:12px; min-width:0; }
  .grp button { padding:5px 12px; border:none; border-radius:6px; background:#22c55e; color:#fff; font-size:12px; cursor:pointer; }
  .recent { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
  .recent span { background:#eef2ff; color:#4338ca; padding:3px 10px; border-radius:20px; font-size:11px; cursor:pointer; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🤖 QQ 机器人后台</h1>
  <div class="sub">本地管理面板 · 自动刷新</div>

  <div class="cards">
    <div class="card">
      <h2>机器人状态</h2>
      <div id="bot-badge" class="badge off">检测中…</div>
      <div style="margin-top:10px;font-size:13px;color:#666">
        <div>名称：<b id="bot-name">-</b></div>
        <div>ID：<span id="bot-id">-</span></div>
        <div>最近就绪：<span id="bot-ready">-</span></div>
      </div>
    </div>
    <div class="card">
      <h2>网络 / 白名单</h2>
      <div>当前公网 IP：<b id="cur-ip">-</b></div>
      <div style="margin-top:6px" id="ip-status">检测中…</div>
      <div class="tip">公网 IP 变化后需到开放平台「IP 白名单」更新</div>
    </div>
    <div class="card">
      <h2>内网穿透 / 回调地址</h2>
      <div>隧道状态：<b id="tunnel-status">检测中…</b></div>
      <div style="margin-top:6px;word-break:break-all">回调地址：<b id="tunnel-url">-</b></div>
      <div style="margin-top:8px"><button onclick="copyUrl()" style="background:#2563eb;color:#fff;border:none;padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer">复制地址</button></div>
      <div class="tip">隧道进程保持运行时地址不变；重启 bot 不会影响地址。仅当 cloudflared 进程被关闭后重新启动时，地址才会变化，需同步更新开放平台回调配置</div>
    </div>
  </div>

  <div class="card" style="margin-bottom:20px">
    <h2>功能开关与群范围（点击切换启用；下方设置某功能的黑白名单）</h2>
    <div id="cmd-list">加载中…</div>
    <div class="tip">💡 群 openid 可点下方面包直接填入当前正在编辑的功能。⚠️ 面板默认不指向任何功能：请先【点击你要设置的某个功能的输入框】让面板选中它，再点群面包才会填对。"仅禁用列表内"=黑名单（这些群不能用）；"仅允许列表内"=白名单（只有这些群能用）。</div>
    <div class="recent" id="recent-groups">加载中…</div>
  </div>

  <div class="card">
    <h2>运行日志（botpy.log 尾部）</h2>
    <div class="log" id="log-box">加载中…</div>
  </div>

  <div class="card" style="margin:20px 0">
    <h2>AI 对话接入（黑猫群友）
      <label class="switch" style="vertical-align:middle;display:inline-block">
        <input type="checkbox" id="ai-enabled" onchange="saveAi()"><span class="slider"></span>
      </label>
      <span id="ai-enable-label" style="font-size:12px;color:#888"></span>
    </h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;margin-top:8px">
      <div>
        <div class="help">服务商 / Provider</div>
        <select id="ai-provider" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db" onchange="sceneProvider()">
          <option value="deepseek">DeepSeek</option>
          <option value="siliconflow">硅基流动 SiliconFlow</option>
          <option value="openai">OpenAI</option>
          <option value="other">其他（自定义兼容端点）</option>
        </select>
      </div>
      <div>
        <div class="help">API Key</div>
        <input type="password" id="ai-key" placeholder="sk-..." style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db">
      </div>
      <div>
        <div class="help">Base URL（OpenAI 兼容端点）</div>
        <input type="text" id="ai-base" placeholder="https://api.deepseek.com" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db">
      </div>
      <div>
        <div class="help">模型 Model</div>
        <div style="display:flex;gap:6px">
          <input type="text" id="ai-model" placeholder="deepseek-chat" style="flex:1;padding:6px;border-radius:6px;border:1px solid #d1d5db">
          <button onclick="fetchAiModels()" style="background:#6366f1;color:#fff;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer">获取模型</button>
        </div>
        <select id="ai-model-list" style="width:100%;margin-top:6px;padding:5px;border-radius:6px;border:1px solid #d1d5db" onchange="document.getElementById('ai-model').value=this.value"></select>
      </div>
    </div>
    <div style="margin-top:10px">
      <div class="help">预设人设 / Prompt（可自由改写，AI 会按它来当群友；保存后生效）</div>
      <textarea id="ai-preset" rows="4" style="width:100%;padding:8px;border-radius:8px;border:1px solid #d1d5db;font-size:13px"></textarea>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px">
      <div><div class="help">保留对话轮数</div><input type="number" id="ai-history" min="2" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db"></div>
      <div><div class="help">记忆总结间隔（0=关闭）</div><input type="number" id="ai-interval" min="0" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db"></div>
      <div><div class="help">温度</div><input type="number" id="ai-temp" step="0.05" min="0" max="2" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db"></div>
    </div>
    <div style="display:flex;gap:10px;margin-top:12px;align-items:center;flex-wrap:wrap">
      <button onclick="saveAi()" style="background:#22c55e;color:#fff;border:none;padding:8px 18px;border-radius:8px;font-size:13px;cursor:pointer">保存配置</button>
      <button onclick="aiTest()" style="background:#2563eb;color:#fff;border:none;padding:8px 18px;border-radius:8px;font-size:13px;cursor:pointer">测试连接</button>
      <span id="ai-feedback" style="font-size:12px;color:#888"></span>
    </div>
    <div style="margin-top:10px;border-top:1px solid #f1f1f1;padding-top:8px;display:flex;gap:10px;align-items:center">
      <b style="font-size:13px">账户余额</b>
      <span id="ai-bal">-</span>
      <button onclick="loadBalance()" style="font-size:12px;padding:4px 10px;border:none;border-radius:6px;background:#e5e7eb;cursor:pointer">刷新余额</button>
    </div>
    <div style="margin-top:12px;border-top:1px solid #f1f1f1;padding-top:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b style="font-size:13px">用户记忆 / 评价（AI 自动总结，程序仅保存）</b>
        <button onclick="loadMem()" style="font-size:12px;padding:4px 10px;border:none;border-radius:6px;background:#e5e7eb;cursor:pointer">刷新</button>
      </div>
      <div id="ai-mem" style="margin-top:8px;font-size:13px;color:#555">加载中…</div>
    </div>
  </div>

  <div class="card" style="margin-top:20px;display:flex;justify-content:space-between;align-items:center">
    <div>
      <div style="font-weight:600">关闭机器人</div>
      <div class="tip">点击后程序会安全退出，需要再次启动时运行 python main.py</div>
    </div>
    <button id="shutdown-btn" onclick="doShutdown()" style="background:#ef4444;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:14px;cursor:pointer">关闭机器人</button>
  </div>
</div>

<script>
function subSwitch(s){
  return `
  <div style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:#f8fafc;border-radius:8px;border:1px solid #eef2ff">
    <span style="font-size:13px">${s.title}</span>
    <label class="switch" style="width:38px;height:20px">
      <input type="checkbox" ${s.enabled?'checked':''} onchange="toggle('${s.name}', this.checked)">
      <span class="slider" style="height:20px"></span>
    </label>
  </div>`;
}
function pluginRow(s){
  const inner = (s.sub && s.sub.length)
    ? `<div style="display:flex;flex-wrap:wrap;gap:8px;margin-left:20px;margin-top:8px">${s.sub.map(subSwitch).join('')}</div>`
    : '';
  return `<div style="margin-bottom:8px">${subSwitch(s)}${inner}</div>`;
}
async function refresh(){
  try{
    const r = await fetch('/api/status');
    const d = await r.json();
    const badge = document.getElementById('bot-badge');
    badge.textContent = d.online ? '● 在线' : '○ 离线';
    badge.className = 'badge ' + (d.online ? 'on' : 'off');
    document.getElementById('bot-name').textContent = d.bot_name;
    document.getElementById('bot-id').textContent = d.bot_id;
    document.getElementById('bot-ready').textContent = d.last_ready || '-';
    document.getElementById('cur-ip').textContent = d.ip;
    const ipst = document.getElementById('ip-status');
    if(d.ip_ok){ ipst.innerHTML = '<span class="ip-ok">✓ 已在白名单</span>'; }
    else { ipst.innerHTML = '<span class="ip-bad">✗ 未在白名单，机器人无法上线</span>'; }
    const tst = document.getElementById('tunnel-status');
    if(d.tunnel_running){ tst.innerHTML = '<span class="ip-ok">● 运行中</span>'; }
    else { tst.innerHTML = '<span class="ip-bad">○ 未运行</span>'; }
    document.getElementById('tunnel-url').textContent = d.tunnel_url || '获取中…';
    const list = document.getElementById('cmd-list');
    list.innerHTML = d.commands.map(c => {
      const gr = c.group_rule || {};
      const mode = gr.mode || '';
      const groups = (gr.groups||[]).join(', ');
      const dlId = 'dl-' + c.name;
      const recentOpt = Object.keys(d.recent_groups||{}).map(g => `<option value="${g}">${g}（${d.recent_groups[g]}）</option>`).join('');
      // 模块内可选参数（可选项放在所属模块的开关行下，和其他模块输入框互不干扰）
      let extra = '';
      if (c.name === 'cmd_bilibili') {
        extra = `
      <div class="grp">
        <span style="font-size:12px;color:#666">解析模式：</span>
        <select id="bili-mode" onchange="setBiliMode()" style="padding:5px 8px;border-radius:6px;border:1px solid #d1d5db;font-size:12px">
          <option value="auto">自动解析</option>
          <option value="passive">被动解析（仅@或私聊）</option>
        </select>
        <span id="bili-mode-label" style="font-size:12px;color:#888"></span>
      </div>`;
      } else if (c.name === 'parse_enabled') {
        extra = `
      <div style="padding:4px 0 10px;border-top:1px dashed #eef2ff;margin-top:2px">
        <div style="display:flex;flex-wrap:wrap;gap:8px" id="parse-plats">
          <span style="color:#888;font-size:12px">加载中…</span>
        </div>
      </div>`;
      } else if (c.name === 'cmd_randomimg') {
        extra = `
      <div style="padding:4px 0 10px;border-top:1px dashed #eef2ff;margin-top:2px">
        <div style="display:flex;flex-wrap:wrap;gap:8px">${(c.sub||[]).map(s=>`
        <div style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:#f8fafc;border-radius:8px;border:1px solid #eef2ff">
          <span style="font-size:13px">${s.title}</span>
          <label class="switch" style="width:38px;height:20px">
            <input type="checkbox" ${s.enabled?'checked':''} onchange="toggle('${s.name}', this.checked)">
            <span class="slider" style="height:20px"></span>
          </label>
        </div>`).join('')}</div>
        <div id="lolicon-filters" data-filters="${(d.lolicon_filters||{}).nsfw===false?0:1},${(d.lolicon_filters||{}).racy===false?0:1}" style="margin-top:8px"></div>
        <div style="font-size:11px;color:#999;margin-top:6px">总开关一键开/关全部图源；下方小开关可单独控制每个图源</div>
      </div>`;
      } else if (c.name === 'cmd_game' || c.name === 'cmd_other') {
        const subs = c.sub||[];
        const flat = subs.filter(s => !(s.sub && s.sub.length));
        const nested = subs.filter(s => s.sub && s.sub.length);
        extra = `
      <div style="padding:4px 0 10px;border-top:1px dashed #eef2ff;margin-top:2px">
        <div style="display:flex;flex-wrap:wrap;gap:8px">${flat.map(subSwitch).join('')}</div>
        ${nested.length ? `<div style="display:flex;flex-direction:column;gap:2px;margin-top:2px">${nested.map(pluginRow).join('')}</div>` : ''}
        <div style="font-size:11px;color:#999;margin-top:6px">总开关一键开/关整个模块；下方每个插件一个总开关，不逐条列底层命令</div>
      </div>`;
      }
      const cTitle = c.title || (c.keywords && c.keywords.length ? c.keywords.join(' / ') : c.name);
      // 模块（随机图片 / 游戏娱乐 / 其他功能）没有独立群黑白名单，不显示该行，避免误操作
      const grpBlock = (c.name === 'cmd_randomimg' || c.name === 'cmd_game' || c.name === 'cmd_other') ? '' : `
      <div class="grp">
        <select id="gr-mode-${c.name}" onchange="saveGroup('${c.name}')">
          <option value="" ${mode===''?'selected':''}>全部群</option>
          <option value="black" ${mode==='black'?'selected':''}>仅禁用列表内</option>
          <option value="white" ${mode==='white'?'selected':''}>仅允许列表内</option>
        </select>
        <input id="gr-groups-${c.name}" list="${dlId}" placeholder="群openid，逗号分隔" value="${groups}" onfocus="activeName='${c.name}'">
        <button onclick="saveGroup('${c.name}')">保存</button>
      </div>`;
      return `
      <datalist id="${dlId}">${recentOpt}</datalist>
      <div class="row" style="padding-bottom:4px">
        <div>
          <div class="kws">${cTitle}</div>
          <div class="help">${c.help}</div>
        </div>
        <label class="switch">
          <input type="checkbox" ${c.enabled?'checked':''} onchange="toggle('${c.name}', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
      ${grpBlock}
      ${extra}`;
    }).join('');
    const gs = d.recent_groups || {};
    const rc = Object.keys(gs).length
      ? Object.keys(gs).map(g => `<span onclick="addRecentTo('${g}')" title="${gs[g]}">${g.slice(0,10)}…</span>`).join('')
      : '<span style="cursor:default;background:#e5e7eb;color:#666">暂无（群里有条消息后自动出现）</span>';
    document.getElementById('recent-groups').innerHTML = rc;
    loadParsePlats(); loadBiliMode(); syncAiToggle(); renderLoliconFilters();
    document.getElementById('log-box').textContent = d.log_tail.join('') || '(暂无日志)';
  }catch(e){
    document.getElementById('log-box').textContent = '连接后台失败: ' + e;
  }
}
async function toggle(name, enabled){
  await fetch('/api/toggle', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, enabled})
  });
}
async function loadParsePlats(){
  try{
    const d = await (await fetch('/api/parse/platforms')).json();
    const box = document.getElementById('parse-plats');
    if(!box) return;
    if(!d.platforms || !d.platforms.length){ box.innerHTML = '<span style="color:#999">（无可控平台）</span>'; return; }
    box.innerHTML = d.platforms.map(p=>`
      <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-radius:8px;border:1px solid #eef2ff">
        <b style="font-size:13px">${p.name}</b>
        <label class="switch" style="width:38px;height:20px">
          <input type="checkbox" ${p.enable?'checked':''} onchange="toggleParsePlat('${p.key}', this.checked)">
          <span class="slider" style="height:20px"></span>
        </label>
      </div>`).join('');
  }catch(e){ const box = document.getElementById('parse-plats'); if(box) box.innerHTML = '<span style="color:#ef4444">加载失败</span>'; }
}
async function toggleParsePlat(name, enabled){
  const d = await (await fetch('/api/parse/platforms', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, enabled})
  })).json();
  setTimeout(loadParsePlats, 400);
}
function renderLoliconFilters(){
  const box = document.getElementById('lolicon-filters');
  if(!box) return;
  const [nsfw, racy] = (box.getAttribute('data-filters')||'1,1').split(',').map(v=>v==='1');
  box.innerHTML = '<div style="font-size:12px;color:#666;margin-bottom:4px">Lolicon 过滤：</div>' +
    'nsfw|NSFW,racy|擦边'.split(',').map(kv=>{
      const [name, label] = kv.split('|');
      const on = (name==='nsfw')?nsfw:racy;
      return `<div style="display:flex;align-items:center;gap:6px;padding:4px 10px;background:#fff7ed;border-radius:8px;border:1px solid #fed7aa;margin-right:8px">
        <span style="font-size:13px">${label}</span>
        <label class="switch" style="width:38px;height:20px">
          <input type="checkbox" ${on?'checked':''} onchange="setLoliconFilter('${name}', this.checked)">
          <span class="slider" style="height:20px"></span>
        </label>
      </div>`;
    }).join('');
}
async function setLoliconFilter(name, enabled){
  await fetch('/api/lolicon/filters', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, enabled})
  });
}
async function loadBiliMode(){
  try{
    const d = await (await fetch('/api/bilibili/mode')).json();
    const sel = document.getElementById('bili-mode');
    sel.value = d.mode || 'auto';
    document.getElementById('bili-mode-label').textContent = d.mode==='passive'
      ? '（当前：被动，仅@或私聊解析）' : '（当前：自动）';
  }catch(e){}
}
async function setBiliMode(){
  const mode = document.getElementById('bili-mode').value;
  const d = await (await fetch('/api/bilibili/mode', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({mode})
  })).json();
  document.getElementById('bili-mode-label').textContent = d.mode==='passive'
    ? '（当前：被动，仅@或私聊解析）' : '（当前：自动）';
}
let activeName = '';
async function saveGroup(name){
  activeName = name;  // 一旦保存某功能，面板就指向它，群面包不会再串到别处
  const mode = document.getElementById('gr-mode-'+name).value;
  const groups = document.getElementById('gr-groups-'+name).value;
  await fetch('/api/group_rule', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, mode, groups})
  });
}
async function addRecentTo(g){
  if(!activeName){ alert('请先点击你要设置的功能的输入框（点进它的群openid输入框），再点这个群'); return; }
  const inp = document.getElementById('gr-groups-'+activeName);
  if(!inp){ activeName=''; alert('请先点击你要设置的功能的输入框，再点这个群'); return; }
  const cur = inp.value ? inp.value.replace(/[ ,，]+/g, ', ').replace(/,\s*$/,'') + ', ' : '';
  inp.value = cur + g;
  saveGroup(activeName);
}
async function doShutdown(){
  if(!confirm('确定要关闭机器人吗？关闭后需要重新运行 python main.py 才能启动。')) return;
  const btn = document.getElementById('shutdown-btn');
  btn.disabled = true;
  btn.textContent = '正在关闭…';
  try{
    await fetch('/api/shutdown', {method:'POST'});
  }catch(e){}
  setTimeout(()=>{ btn.textContent = '已关闭，页面即将断开'; }, 1500);
}
async function copyUrl(){
  const url = document.getElementById('tunnel-url').textContent;
  if(!url || url === '获取中…'){ alert('地址还没获取到，稍等几秒再试'); return; }
  try{
    await navigator.clipboard.writeText(url);
    alert('已复制: ' + url);
  }catch(e){
    alert('复制失败，请手动复制: ' + url);
  }
}
// ---------- AI 对话配置 ----------
// 自动刷新时同步 AI 开关状态，避免页面显示与实际开关不一致（只更新开关和提示文字，不覆盖未保存的输入框）
async function syncAiToggle(){
  try{
    const d = await (await fetch('/api/ai/config')).json();
    const cb = document.getElementById('ai-enabled');
    if(!cb) return;
    cb.checked = !!d.enabled;
    document.getElementById('ai-enable-label').textContent = d.enabled ? '● 已启用（@机器人 或私聊触发）' : '○ 未启用';
  }catch(e){}
}
async function loadAi(){
  try{
    const d = await (await fetch('/api/ai/config')).json();
    document.getElementById('ai-enabled').checked = !!d.enabled;
    document.getElementById('ai-provider').value = d.provider || 'deepseek';
    document.getElementById('ai-key').value = d.api_key || '';
    document.getElementById('ai-base').value = d.base_url || '';
    document.getElementById('ai-model').value = d.model || '';
    document.getElementById('ai-preset').value = d.system_preset || '';
    document.getElementById('ai-history').value = d.max_history || 12;
    document.getElementById('ai-interval').value = d.memory_interval || 5;
    document.getElementById('ai-temp').value = d.temperature || 0.85;
    document.getElementById('ai-enable-label').textContent = d.enabled ? '● 已启用（@机器人 或私聊触发）' : '○ 未启用';
  }catch(e){}
}
function sceneProvider(){
  const pre = {deepseek:'https://api.deepseek.com', siliconflow:'https://api.siliconflow.cn/v1', openai:'https://api.openai.com/v1', other:''};
  const v = document.getElementById('ai-provider').value;
  const def = pre[v];
  if(def && !document.getElementById('ai-base').value.trim()) document.getElementById('ai-base').value = def;
}
async function saveAi(){
  const body = {
    enabled: document.getElementById('ai-enabled').checked,
    provider: document.getElementById('ai-provider').value,
    api_key: document.getElementById('ai-key').value.trim(),
    base_url: document.getElementById('ai-base').value.trim(),
    model: document.getElementById('ai-model').value.trim(),
    system_preset: document.getElementById('ai-preset').value,
    max_history: parseInt(document.getElementById('ai-history').value)||12,
    memory_interval: parseInt(document.getElementById('ai-interval').value)||5,
    temperature: parseFloat(document.getElementById('ai-temp').value)||0.85
  };
  const d = await (await fetch('/api/ai/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('ai-feedback').textContent = d.ok ? '✓ 已保存' : '保存失败';
  document.getElementById('ai-enable-label').textContent = body.enabled ? '● 已启用' : '○ 未启用';
  loadBalance(); loadMem();
}
async function aiTest(){
  document.getElementById('ai-feedback').textContent = '测试中…';
  const d = await (await fetch('/api/ai/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
  if(d.ok) document.getElementById('ai-feedback').textContent = '✓ 连接正常，回话：' + d.reply;
  else document.getElementById('ai-feedback').textContent = '✗ ' + (d.msg||'失败');
}
async function fetchAiModels(){
  document.getElementById('ai-feedback').textContent = '拉取模型中…';
  const d = await (await fetch('/api/ai/models',{method:'POST'})).json();
  const sel = document.getElementById('ai-model-list');
  if(d.ok && d.models && d.models.length){
    sel.innerHTML = d.models.map(m=>`<option value="${m}">${m}</option>`).join('');
    document.getElementById('ai-feedback').textContent = '✓ 共 ' + d.models.length + ' 个模型，可从下拉选择';
  } else {
    document.getElementById('ai-feedback').textContent = '✗ 获取模型失败：' + (d.msg||'');
  }
}
async function loadBalance(){
  try{
    const d = await (await fetch('/api/ai/balance')).json();
    const b = d.balance;
    document.getElementById('ai-bal').textContent = b ? ('¥ ' + b.total + (b.currency?' '+b.currency:'') + '（' + b.provider + '）') : '不支持或查询失败';
  }catch(e){ document.getElementById('ai-bal').textContent = '查询失败'; }
}
async function loadMem(){
  const d = await (await fetch('/api/ai/memory')).json();
  const mem = d.memory || {};
  const keys = Object.keys(mem);
  const box = document.getElementById('ai-mem');
  if(!keys.length){ box.innerHTML = '<span style="color:#999">（暂无记忆，聊过几轮后 AI 会自动总结）</span>'; return; }
  box.innerHTML = keys.map(oid=>{
    const m = mem[oid];
    return `<div style="border:1px solid #f1f1f1;border-radius:8px;padding:8px 10px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>${escapeHtml(m.nickname)||oid.slice(0,10)}</b>
        <button onclick="delMem('${oid}')" style="font-size:11px;padding:3px 8px;border:none;border-radius:5px;background:#fee2e2;color:#b91c1c;cursor:pointer">删除</button>
      </div>
      <div style="margin-top:4px;color:#333">📝 ${escapeHtml(m.memory||'-')}</div>
      <div style="margin-top:2px;color:#888">⭐ ${escapeHtml(m.summary||'-')}</div>
      ${m.portrait?`<div style="margin-top:2px;color:#7c3aed">🎭 ${escapeHtml(m.portrait)}</div>`:''}
      ${m.relations?`<div style="margin-top:2px;color:#0d9488">🕸️ ${escapeHtml(m.relations)}</div>`:''}
      <div style="font-size:11px;color:#bbb;margin-top:2px">${oid}</div>
    </div>`;
  }).join('');
}
function delMem(oid){
  if(!confirm('确定删除该用户的记忆吗？')) return;
  fetch('/api/ai/memory/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({openid:oid})}).then(()=>loadMem());
}
function escapeHtml(s){ return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
loadAi(); loadBalance(); loadMem(); loadParsePlats(); loadBiliMode();
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


async def start_webui(bot, port=WEBUI_PORT, tunnel=None):
    """启动 Web 后台，返回 (runner, site)。"""
    ui = WebUI(bot, tunnel=tunnel)
    runner = web.AppRunner(ui.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[UI] 后台已启动: http://127.0.0.1:{port}")
    return runner, site