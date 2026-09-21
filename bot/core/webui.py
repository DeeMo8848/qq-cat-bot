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
from config import WEBUI_PORT, ROOT, WEBUI_BIND, _display_host

# 公网 IP 查询源（按顺序尝试；国内源在前，开代理/VPN 时比国际源稳得多）
_IP_PROVIDERS = [
    "https://myip.ipip.net",
    "https://ip.3322.net",
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://api.ip.sb/ip",
]

# 从各家返回值里抠出 IPv4（有的源是纯 IP，有的带「当前 IP：x.x.x.x 来自于：…」这类包装）
_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")

_LOG_FILE = os.path.join(ROOT, "botpy.log")


def _module_groups():
    """返回 Web 后台「游戏娱乐 / 随机图片 / 其他功能 / 测试功能」功能树及其命令集合。

    - GAME_GROUPS: [(key, 名, [命令名])]，游戏娱乐下每个功能整体一个开关
    - GAME_CMD_NAMES: 全部游戏功能命令（用于隐藏与一键开关）
    - OTHER_PLUGINS: [(key, 名, [命令名], sub)]，其他功能下每个插件整体一个开关；
      sub 供插件内还有独立功能的嵌套开关（仅搜图），格式同 GAME_GROUPS
    - OTHER_CMD_NAMES: 全部其他功能命令（用于隐藏与一键开关）
    - TEST_PLUGINS: [(key, 名, [命令名])]，测试功能下每个插件一个总开关
    - TEST_CMD_NAMES: 全部测试功能命令（用于隐藏与一键开关）
    - MENU_HIDDEN: 仅从后台隐藏、不出独立开关的命令（子菜单入口 + 已并入其他模块的分项）
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
    from plugins.cards import CARD_CMD_NAMES

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
        # 「下载图片 / 下载表情」归入「其他功能」
        ("download_image", "下载图片/表情", ["cmd_download_image"], []),
    ]
    other_names = {n for _, _, ns, _ in other_plugins for n in ns}
    # 「其他功能」子菜单入口命令（cmd_other_menu）并入该模块，避免与模块卡片重复显示
    other_names = other_names | {"cmd_other_menu"}

    # 游戏娱乐：吉星派对 / 21点 / 海龟汤 / 钓鱼（含买&升级钓鱼机）/ 卡牌制作
    # 注：卡牌制作并入游戏娱乐后，「游戏娱乐」总开关会一并控制卡牌命令
    game_groups = list(GAME_GROUPS) + [
        ("fishing", "钓鱼系统", sorted(FISHING_CMD_NAMES | {"cmd_buy_machine", "cmd_upgrade_machine"})),
        ("cards", "卡牌制作", sorted(CARD_CMD_NAMES)),
    ]
    game_names = set(GAME_CMD_NAMES) | FISHING_CMD_NAMES | set(CARD_CMD_NAMES) \
        | {"cmd_buy_machine", "cmd_upgrade_machine"}

    # 测试功能：打招呼 / 自更新 / 运维（状态&重启）/ 版本查询 / 网页测试
    test_plugins = [
        ("test_hello", "打招呼", ["cmd_hello"]),
        ("test_update", "bot 更新", ["cmd_bot_update"]),
        ("test_ops_status", "bot 状态", ["cmd_bot_status"]),
        ("test_ops_restart", "重启 bot", ["cmd_bot_restart"]),
        ("test_version", "版本查询", ["cmd_bot_version"]),
        ("test_webtest", "网页测试", ["cmd_webtest"]),
    ]
    test_names = {n for _, _, ns in test_plugins for n in ns}

    # 仅隐藏、不出开关的命令：
    #   1) 各模块的「子菜单入口」命令（游戏/随机图/其他功能的清单），
    #      它们与对应的模块卡片重复，不需要单独一个开关；
    #   2) 已并入其他模块的分项（登录b站 → 属 B站解析，跟随 cmd_bilibili 开关）；
    #   3) 游戏娱乐下的各功能清单命令（随机星趴/21点/海龟汤/钓鱼系统/卡牌制作），
    #      已作为游戏娱乐的二级开关展示，不再单列顶层卡片。
    menu_hidden = {
        "cmd_game_menu",        # 游戏娱乐清单（并入 cmd_game）
        "cmd_random_menu",      # 随机图片清单（并入 cmd_randomimg）
        "cmd_other_menu",       # 其他功能清单（并入 cmd_other）
        "cmd_bili_login",       # 登录b站 / B站登录 → 属 B站解析
        "cmd_game_menu_star",   # 随机星趴清单
        "cmd_game_menu_blackjack",  # 21点清单
        "cmd_game_menu_turtle",     # 海龟汤清单
        "cmd_game_menu_fishing",    # 钓鱼系统清单
        "cmd_game_menu_card",       # 卡牌制作清单
    }

    return (game_groups, game_names, other_plugins, other_names,
            test_plugins, test_names, menu_hidden)


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
    # 对外绑定告警只打印一次（避免每条请求刷屏）
    _warned = False

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
        self.app.router.add_get("/api/ai/providers", self.ai_providers)
        self.app.router.add_post("/api/ai/config", self.ai_save_config)
        self.app.router.add_post("/api/ai/test", self.ai_test)
        self.app.router.add_post("/api/ai/models", self.ai_models)
        self.app.router.add_get("/api/ai/balance", self.ai_balance)
        self.app.router.add_get("/api/ai/memory", self.ai_memory)
        self.app.router.add_post("/api/ai/memory/delete", self.ai_memory_delete)
        # 管理分页 API（卡牌 / 钓鱼 / 用户数据）
        self.app.router.add_get("/api/admin/cards", self.admin_cards)
        self.app.router.add_post("/api/admin/cards", self.admin_cards_save)
        self.app.router.add_get("/api/admin/fishing", self.admin_fishing)
        self.app.router.add_post("/api/admin/fishing", self.admin_fishing_save)
        self.app.router.add_post("/api/admin/fishing/reset", self.admin_fishing_reset)
        self.app.router.add_get("/api/admin/users", self.admin_users)
        self.app.router.add_post("/api/admin/users/balance", self.admin_users_balance)
        self.app.router.add_post("/api/admin/users/reset_fishing", self.admin_users_reset_fishing)
        # 随机一图预览代理（供独立预览网页按 source 取一张图）
        self.app.router.add_get("/api/randomimg/preview", self.randomimg_preview)
        self.app.middlewares.append(self._bind_guard)
        self._ip = None
        self._ip_time = 0.0
        self._ip_error_time = 0.0

    @web.middleware
    async def _bind_guard(self, request, handler):
        """非回环绑定时打印一次告警：后台无鉴权，暴露到公网 = 任何人可操作。

        不阻断请求（用户明确配置了 BIND_ADDR 就尊重该选择），只做醒目提醒，
        避免"改了绑定地址却不知道后果"。
        """
        if WEBUI_BIND not in ("127.0.0.1", "localhost") and not WebUI._warned:
            WebUI._warned = True
            print("=" * 60)
            print("[安全警告] WebUI 绑定在 %s，已对外网开放！" % WEBUI_BIND)
            print("           后台【没有任何身份验证】，任何能访问该端口的人")
            print("           都可以开关插件、改配置、操作玩家数据、关闭机器人。")
            print("           请确认云安全组已限制来源 IP，或改用隧道访问。")
            print("=" * 60)
        return await handler(request)

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
        rand_names = randomimg.RANDOMIMG_CMD_NAMES
        (GAME_GROUPS, GAME_CMD_NAMES, OTHER_PLUGINS, OTHER_CMD_NAMES,
         TEST_PLUGINS, TEST_CMD_NAMES, MENU_HIDDEN) = _module_groups()
        rand_cmds = [f for f in commands._COMMANDS if f.__name__ in rand_names]
        # 属于各模块/插件的底层命令不留独立开关，统一归为插件的总开关
        # 注：卡牌（CARD_CMD_NAMES）已并入游戏娱乐；测试功能命令收进 TEST_CMD_NAMES
        #     MENU_HIDDEN 为子菜单入口与已并入其他模块的分项（仅隐藏）
        hidden = (rand_names | GAME_CMD_NAMES | OTHER_CMD_NAMES | TEST_CMD_NAMES
                  | MENU_HIDDEN)
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
            "help": "游戏娱乐（随机星趴 / 21点 / 海龟汤 / 钓鱼系统 / 卡牌制作）",
            "enabled": any(state.is_enabled(n) for n in GAME_CMD_NAMES),
            "group_rule": None,
            "sub": [_plugin_switch(k, t, ns, []) for k, t, ns in GAME_GROUPS],
        })
        # 「其他功能」插件树：每个插件一个总开关；搜图插件内含搜番/搜角色/搜出处子开关
        commands_list.append({
            "name": "cmd_other",
            "title": "其他功能",
            "keywords": [],
            "help": "其他功能（搜图 / 漂流瓶 / 吃什么 / MC皮肤 / 幻影坦克 / emojimix / 网易云点歌 / 今日运势 / 随机文案 / 下载图片）",
            "enabled": any(state.is_enabled(n) for n in OTHER_CMD_NAMES),
            "group_rule": None,
            "sub": [_plugin_switch(k, t, ns, sub) for k, t, ns, sub in OTHER_PLUGINS],
        })
        # 「测试功能」模块：打招呼 / bot 更新 / 运维状态 / 重启 / 版本查询 / 网页测试
        commands_list.append({
            "name": "cmd_test",
            "title": "测试功能",
            "keywords": [],
            "help": "测试功能（打招呼 / bot 更新 / bot 状态 / 重启 bot / 版本查询 / 网页测试）",
            "enabled": any(state.is_enabled(n) for n in TEST_CMD_NAMES),
            "group_rule": None,
            "sub": [_plugin_switch(k, t, ns, []) for k, t, ns in TEST_PLUGINS],
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
        (GAME_GROUPS, GAME_CMD_NAMES, OTHER_PLUGINS, OTHER_CMD_NAMES,
         TEST_PLUGINS, TEST_CMD_NAMES, MENU_HIDDEN) = _module_groups()
        # 插件/功能组开关 key -> 该开关下所有命令名（含搜图插件的子开关）
        group_map = {}
        for key, _title, names, sub in OTHER_PLUGINS:
            group_map[key] = set(names)
            for k, _t, ns in sub:
                group_map[k] = set(ns)
        for key, _title, names in GAME_GROUPS:
            group_map[key] = set(names)
        for key, _title, names in TEST_PLUGINS:
            group_map[key] = set(names)
        known = (
            {f.__name__ for f in commands._COMMANDS}
            | {"parse_enabled", "cmd_randomimg", "cmd_game", "cmd_other", "cmd_test"}
            | set(group_map)
            | MENU_HIDDEN
        )
        if name not in known:
            return web.json_response({"ok": False, "msg": "命令不存在"}, status=400)
        if name in group_map:
            # 插件/功能组开关（如 emojimix / 21点 / 搜番）：一键开/关该插件下所有命令
            # 注：钓鱼组已含 cmd_buy_machine / cmd_upgrade_machine（原独立分项）
            for n in group_map[name]:
                state.set_enabled(n, enabled)
        elif name == "cmd_randomimg":
            # 「随机图片」模块总开关：一键开/关所有随机图子命令（含随机图清单入口）
            for n in randomimg.RANDOMIMG_CMD_NAMES | {"cmd_random_menu"}:
                state.set_enabled(n, enabled)
        elif name == "cmd_game":
            # 「游戏娱乐」模块总开关：一键开/关所有游戏子命令（含钓鱼、卡牌与各功能清单）
            for n in GAME_CMD_NAMES | {"cmd_game_menu"}:
                state.set_enabled(n, enabled)
        elif name == "cmd_other":
            # 「其他功能」模块总开关：一键开/关其余所有功能命令（含其他功能清单入口）
            for n in OTHER_CMD_NAMES | {"cmd_other_menu"}:
                state.set_enabled(n, enabled)
        elif name == "cmd_test":
            # 「测试功能」模块总开关：一键开/关打招呼、更新、版本、网页测试
            for n in TEST_CMD_NAMES:
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

    async def ai_providers(self, request):
        """可选服务商列表（供前端渲染下拉框与默认值）。"""
        try:
            return web.json_response({"ok": True, "providers": ai_mod.list_providers()})
        except Exception as e:
            return web.json_response({"ok": False, "msg": str(e), "providers": []})

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

        跨域：仅对来自本机的页面（file:// 的 Origin: null / file://、127.0.0.1 / localhost）
        放行 Access-Control-Allow-Origin，让预览页能用 fetch 拿到图片字节，
        实现「保存的正是当前这张图」。其他来源不回显，避免任意网站读取本机接口。
        注：file:// 页面用 "*"（回显 "null" 在部分启动参数下会被浏览器判为不匹配）。
        """
        headers = {"Cache-Control": "no-store"}
        origin = request.headers.get("Origin", "")
        if origin in ("null", "file://", ""):
            headers["Access-Control-Allow-Origin"] = "*"
        elif origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        headers["Access-Control-Allow-Methods"] = "GET, HEAD"
        source = request.query.get("source", "")
        if not source:
            return web.Response(status=400, text="缺少 source 参数", headers=headers)
        q = {k: v for k, v in request.query.items()}
        data, ctype = await randomimg.fetch_preview_image(source, **q)
        if not data:
            return web.Response(status=502, text="api死了喵", headers=headers)
        return web.Response(body=data, content_type=ctype or "image/jpeg", headers=headers)

    async def shutdown(self, request):
        """关闭机器人并退出程序（优雅关闭 + 兜底强制退出）。"""
        try:
            await self.bot.close()
        except Exception:
            pass
        # 给清理留一点时间，然后强制退出进程
        asyncio.get_event_loop().call_later(1.0, os._exit, 0)
        return web.json_response({"ok": True, "msg": "正在关闭机器人…"})

    # ---------- 管理分页：卡牌 ----------
    async def admin_cards(self, request):
        from plugins.cards import commands as ccmd
        from plugins.cards import carddata as ccd
        from plugins.cards import forge
        cats = {}
        for cn, (key, _name) in ccmd.MATERIAL_CATS.items():
            if key == "glow":
                cats[key] = {"cn": cn, "count": len(forge.GLOW_BUILTINS)}
            else:
                cats[key] = {"cn": cn, "count": len(forge.list_materials(key))}
        return web.json_response({
            "ok": True,
            "make_cost": ccmd._make_cost(),
            "use_fee": ccmd._use_fee(),
            "cooldown": ccmd._cooldown_min(),
            "material_prices": ccmd._prices(),
            "tax": ccmd._tax(),
            "rarity_ranges": {k: list(v) for k, v in ccd._rarity_ranges().items()},
            "materials": cats,
        })

    async def admin_cards_save(self, request):
        data = await request.json() or {}
        fp = os.path.join(ROOT, "settings.json")
        try:
            with open(fp, "r", encoding="utf-8") as f:
                s = json.load(f) or {}
        except Exception:
            s = {}
        if "make_cost" in data:
            s["CARD_MAKE_COST"] = int(data["make_cost"])
        if "use_fee" in data:
            s["CARD_MATERIAL_USE_FEE"] = max(0, int(data["use_fee"]))
        if "cooldown" in data:
            s["CARD_MAKE_COOLDOWN"] = max(0, int(data["cooldown"]))
        if "material_prices" in data:
            s["CARD_MATERIAL_PRICES"] = {
                k: int(v) for k, v in (data["material_prices"] or {}).items()}
        if "tax" in data:
            s["CARD_MARKET_TAX"] = float(data["tax"])
        if "rarity_ranges" in data:
            s["CARD_RARITY_RANGES"] = {
                k: [int(v[0]), int(v[1])] for k, v in (data["rarity_ranges"] or {}).items()}
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fp)
        return web.json_response({"ok": True})

    # ---------- 管理分页：钓鱼 ----------
    async def admin_fishing(self, request):
        from plugins.fishing import config_store
        return web.json_response({"ok": True, "config": config_store.get_effective()})

    async def admin_fishing_save(self, request):
        from plugins.fishing import config_store
        data = await request.json() or {}
        section = (data.get("section") or "").strip()
        if not section or "data" not in data:
            return web.json_response({"ok": False, "msg": "参数不完整"}, status=400)
        if not config_store.save(section, data["data"]):
            return web.json_response({"ok": False, "msg": f"分区 {section} 不存在"}, status=400)
        return web.json_response({"ok": True})

    async def admin_fishing_reset(self, request):
        from plugins.fishing import config_store
        data = await request.json() or {}
        section = (data.get("section") or "").strip()
        if not section or not config_store.reset(section):
            return web.json_response({"ok": False, "msg": f"分区 {section} 不存在"}, status=400)
        return web.json_response({"ok": True})

    # ---------- 管理分页：用户数据 ----------
    async def admin_users(self, request):
        from bot.core import wallet
        balances = wallet.all_balances()
        fwhole = {}
        fp = os.path.join(ROOT, "data", "fishing", "fishing_data.json")
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    fwhole = json.load(f) or {}
            except Exception:
                fwhole = {}
        fusers = fwhole.get("users") or {}
        exch = fwhole.get("exchange", {}).get("holdings") or {}
        cwhole = {}
        cp = os.path.join(ROOT, "data", "cards", "data.json")
        if os.path.exists(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    cwhole = json.load(f) or {}
            except Exception:
                cwhole = {}
        albums = cwhole.get("albums") or {}
        cards = cwhole.get("cards") or {}
        materials = cwhole.get("materials") or {}

        openids = set(balances) | set(fusers) | set(albums)
        out = []
        for oid in openids:
            u = fusers.get(oid) or {}
            inv = u.get("inventory") or {}
            codex = u.get("codex") or {}
            stats = u.get("stats") or {}
            my_cards = [c for c in cards.values()
                        if c.get("owner") == oid and c.get("status") in ("owned", "market")]
            my_mats = materials.get(oid) or {}
            hold = exch.get(oid) or {}
            out.append({
                "openid": oid,
                "balance": balances.get(oid, 0),
                "nick": albums.get(oid, {}).get("nick", ""),
                "fishing": {
                    "rod": u.get("rod", 1), "hook": u.get("hook", 1),
                    "line": u.get("line", 1), "float": u.get("float", 1),
                    "baits": u.get("baits", {}),
                    "inventory_count": sum(len(w) for w in inv.values()),
                    "codex_count": len(codex),
                    "catches": stats.get("catches", 0),
                    "sold_earn": stats.get("sold_earn", 0),
                    "gacha": stats.get("gacha", 0),
                    "ach_count": len(u.get("achievements") or []),
                    "auto_fish": bool(u.get("auto_fish")),
                    "last_fish": u.get("last_fish", 0),
                },
                "cards": {
                    "count": len(my_cards),
                    "market": sum(1 for c in my_cards if c.get("status") == "market"),
                    "materials": sum(len(v) for v in my_mats.values()),
                },
                "exchange": {"qty": sum(r.get("qty", 0) for r in hold.values()),
                             "kinds": len(hold)},
            })
        out.sort(key=lambda x: -x["balance"])
        return web.json_response({"ok": True, "users": out})

    async def admin_users_balance(self, request):
        from bot.core import wallet
        data = await request.json() or {}
        oid = (data.get("openid") or "").strip()
        if not oid:
            return web.json_response({"ok": False, "msg": "缺少 openid"}, status=400)
        try:
            bal = wallet.set_balance(oid, int(data.get("balance", 0)))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "msg": "金额格式错误"}, status=400)
        return web.json_response({"ok": True, "balance": bal})

    async def admin_users_reset_fishing(self, request):
        from plugins.fishing import core as fcore
        data = await request.json() or {}
        oid = (data.get("openid") or "").strip()
        if not oid:
            return web.json_response({"ok": False, "msg": "缺少 openid"}, status=400)
        with fcore._lock:
            fdata = fcore._load()
            if oid in fdata.get("users", {}):
                del fdata["users"][oid]
            fcore._save(fdata)
        return web.json_response({"ok": True})

    # ---------- 内部工具 ----------
    async def _current_ip(self):
        """获取当前公网出口 IP（成功缓存 60 秒；失败也缓存 60 秒，避免面板每次刷新都白等）。"""
        now = time.time()
        if self._ip and now - self._ip_time < 60:
            return self._ip
        if now - self._ip_error_time < 60 and self._ip_error_time:
            return self._ip or "获取失败"
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                for url in _IP_PROVIDERS:
                    try:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                            text = (await resp.text()).strip()
                            m = _IP_RE.search(text)
                            if m:
                                self._ip = m.group(1)
                                self._ip_time = now
                                self._ip_error_time = 0.0
                                return self._ip
                    except Exception:
                        continue
        except Exception:
            pass
        self._ip_error_time = now
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
  /* ---- 管理分页 ---- */
  .tabs { display:flex; gap:8px; margin-bottom:18px; flex-wrap:wrap; }
  .tab { padding:8px 18px; border:none; border-radius:10px; background:#e2e8f0; color:#475569; font-size:14px; cursor:pointer; }
  .tab.active { background:#2563eb; color:#fff; font-weight:700; }
  .ad-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:10px; }
  .ad-grid label { display:block; font-size:12px; color:#666; margin-bottom:4px; }
  .ad-grid input, .ad-grid select { width:100%; padding:6px 8px; border-radius:6px; border:1px solid #d1d5db; font-size:13px; background:#fff; }
  .ad-btns { display:flex; gap:10px; align-items:center; margin-top:12px; }
  .ad-btns button { padding:7px 16px; border:none; border-radius:8px; background:#22c55e; color:#fff; font-size:13px; cursor:pointer; }
  .ad-btns .rst { background:#f59e0b; }
  .ad-btns .del { background:#ef4444; }
  .fb { font-size:12px; color:#888; }
  table.ad-tb { width:100%; border-collapse:collapse; font-size:13px; }
  table.ad-tb th { background:#f1f5f9; text-align:left; padding:7px 8px; font-size:12px; color:#555; }
  table.ad-tb td { padding:5px 6px; border-bottom:1px solid #f1f1f1; }
  table.ad-tb input, table.ad-tb select { padding:4px 6px; border-radius:5px; border:1px solid #d1d5db; font-size:12px; width:100%; background:#fff; }
  table.ad-tb input.ad-id { background:#f8fafc; color:#64748b; font-family:Consolas,monospace; font-size:11px; }
  .ad-scroll { max-height:520px; overflow:auto; border:1px solid #e2e8f0; border-radius:10px; }
  .ad-note { font-size:12px; color:#888; margin-top:6px; }
  .usr-card { background:#f8fafc; border:1px solid #eef2ff; border-radius:10px; padding:10px 12px; margin-bottom:8px; }
  .usr-head { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }
  .usr-meta { font-size:12px; color:#64748b; margin-top:4px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="tabs">
    <button class="tab active" data-tab="overview" onclick="showTab('overview')">📋 概览</button>
    <button class="tab" data-tab="cards" onclick="showTab('cards')">🃏 卡牌管理</button>
    <button class="tab" data-tab="fishing" onclick="showTab('fishing')">🐟 钓鱼管理</button>
    <button class="tab" data-tab="users" onclick="showTab('users')">👥 用户数据</button>
  </div>
</div>
<div class="wrap tab-panel" id="tab-overview">
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
      <h2>网络</h2>
      <div>当前公网 IP：<b id="cur-ip">-</b></div>
      <div class="tip">如需开启白名单可自行在官方机器人后台添加此 IP</div>
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
        <div class="help" id="ai-provider-hint" style="margin-top:4px"></div>
      </div>
      <div>
        <div class="help">API Key</div>
        <input type="password" id="ai-key" placeholder="sk-..." style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db">
      </div>
      <div>
        <div class="help">Base URL / API 根地址（留空用该服务商默认）</div>
        <input type="text" id="ai-base" placeholder="https://api.deepseek.com" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db">
      </div>
      <div>
        <div class="help">模型 Model（留空用默认）</div>
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
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:10px">
      <div><div class="help">最大输出 tokens（0=用服务商默认；Claude 必填，内部兜底 2048）</div><input type="number" id="ai-maxtokens" min="0" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db"></div>
      <div><div class="help">请求超时（秒）</div><input type="number" id="ai-timeout" min="5" style="width:100%;padding:6px;border-radius:6px;border:1px solid #d1d5db"></div>
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

<!-- ================= 卡牌管理 ================= -->
<div class="wrap tab-panel" id="tab-cards" style="display:none">
  <h1>🃏 卡牌制作 · 后台管理</h1>
  <div class="sub">保存后立即生效，无需重启机器人</div>

  <div class="card" style="margin-bottom:16px">
    <h2>💰 基础费用</h2>
    <div class="ad-grid">
      <div>
        <label>制作费（喵币/次）</label>
        <input type="number" id="card-make-cost" min="0" step="10">
      </div>
      <div>
        <label>素材使用费（喵币/个，0=不加收）</label>
        <input type="number" id="card-use-fee" min="0" step="10">
      </div>
      <div>
        <label>制作冷却（分钟，0=关闭）</label>
        <input type="number" id="card-cooldown" min="0" step="5">
      </div>
      <div>
        <label>市场交易税（0.1 = 10%）</label>
        <input type="number" id="card-tax" min="0" max="0.9" step="0.01">
      </div>
    </div>
    <div class="ad-btns">
      <button onclick="saveCardsBase()">保存基础费用</button>
      <span class="fb" id="card-base-fb"></span>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🎨 素材包价格（喵币/个，购买后永久使用）</h2>
    <div class="ad-grid" id="card-material-prices">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveCardsMaterials()">保存素材价格</button>
      <span class="fb" id="card-mat-fb"></span>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🏷️ 品级价格区间（AI 判定品级后随机出价）</h2>
    <div class="ad-grid" id="card-rarity-ranges">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveCardsRarity()">保存品级区间</button>
      <span class="fb" id="card-rarity-fb"></span>
    </div>
  </div>

  <div class="card">
    <h2>📦 素材一览（数量统计）</h2>
    <div id="card-materials-info">加载中…</div>
  </div>
</div>

<!-- ================= 钓鱼管理 ================= -->
<div class="wrap tab-panel" id="tab-fishing" style="display:none">
  <h1>🐟 钓鱼系统 · 后台管理</h1>
  <div class="sub">保存后立即生效；各分区「恢复默认」可回到代码原始设定</div>

  <div class="card" style="margin-bottom:16px">
    <h2>💹 经济参数</h2>
    <div class="ad-grid" id="fish-economy">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveFishSection('economy')">保存经济参数</button>
      <button class="rst" data-sec="economy" onclick="resetFishSection('economy')">恢复默认</button>
      <span class="fb" id="fb-economy"></span>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🎣 商店装备（钓竿 / 鱼钩 / 鱼线 / 鱼漂 / 鱼饵）</h2>
    <div id="fish-gears">加载中…</div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🐟 鱼种表（共 <span id="fish-count">0</span> 种）
      <span style="font-size:12px;color:#888;font-weight:400">｜搜索 <input id="fish-search" placeholder="名称/ID" style="width:130px;padding:4px 8px;border-radius:6px;border:1px solid #d1d5db;font-size:12px" oninput="renderFishTable()"> 稀有度 <select id="fish-rarity-filter" onchange="renderFishTable()" style="padding:4px 8px;border-radius:6px;border:1px solid #d1d5db;font-size:12px"><option value="">全部</option></select></span>
    </h2>
    <div class="ad-scroll" id="fish-table-wrap">加载中…</div>
    <div class="ad-btns" style="margin-top:10px">
      <button onclick="addFishRow()">➕ 新增鱼种</button>
      <button onclick="saveFishSection('fish')">保存鱼种表</button>
      <button class="rst" data-sec="fish" onclick="resetFishSection('fish')">恢复默认</button>
      <span class="fb" id="fb-fish"></span>
    </div>
    <div class="ad-note">ID 为英文唯一标识（新鱼请填新 ID）；名称/图标/稀有度/基础价/重量区间(克)/时段可直接改；改 ID 后点其他输入框生效。删除点行尾 🗑，保存后才真正生效。</div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🎰 扭蛋概率（各稀有度权重）</h2>
    <div class="ad-grid" id="fish-gacha">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveFishSection('gacha')">保存扭蛋概率</button>
      <button class="rst" data-sec="gacha" onclick="resetFishSection('gacha')">恢复默认</button>
      <span class="fb" id="fb-gacha"></span>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>📈 交易所商品（名称 / 基础价 / 保质期天）</h2>
    <div class="ad-scroll" id="fish-exchange">加载中…</div>
    <div class="ad-btns">
      <button onclick="exAdd()">➕ 新增商品</button>
      <button onclick="saveFishSection('exchange')">保存交易所</button>
      <button class="rst" data-sec="exchange" onclick="resetFishSection('exchange')">恢复默认</button>
      <span class="fb" id="fb-exchange"></span>
    </div>
  </div>

  <div class="card" style="margin-bottom:16px">
    <h2>🐱 社交互动（偷鱼 / 电鱼 / 鱼缸）与 🎣 自动钓鱼</h2>
    <div class="ad-grid" id="fish-social-auto">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveFishSocialAuto()">保存社交/自动</button>
      <button class="rst" data-sec="social" onclick="resetFishSocialAuto()">恢复默认</button>
      <span class="fb" id="fb-socialauto"></span>
    </div>
  </div>

  <div class="card">
    <h2>🎲 赌场参数（骰宝 / 命运之轮 / 擦弹）</h2>
    <div id="fish-gamble">加载中…</div>
    <div class="ad-btns">
      <button onclick="saveFishGamble()">保存赌场参数</button>
      <button class="rst" data-sec="gamble" onclick="resetFishSection('gamble')">恢复默认</button>
      <span class="fb" id="fb-gamble"></span>
    </div>
  </div>
</div>

<!-- ================= 用户数据 ================= -->
<div class="wrap tab-panel" id="tab-users" style="display:none">
  <h1>👥 用户数据 · 后台管理</h1>
  <div class="sub">喵喵币余额可改；「重置钓鱼数据」清空该用户的钓鱼进度（鱼获/图鉴/装备，不可恢复，谨慎使用）</div>
  <div id="users-list">加载中…</div>
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
      } else if (c.name === 'cmd_game' || c.name === 'cmd_other' || c.name === 'cmd_test') {
        const subs = c.sub||[];
        const flat = subs.filter(s => !(s.sub && s.sub.length));
        const nested = subs.filter(s => s.sub && s.sub.length);
        extra = `
      <div style="padding:4px 0 10px;border-top:1px dashed #eef2ff;margin-top:2px">
        <div style="display:flex;flex-wrap:wrap;gap:8px">${flat.map(subSwitch).join('')}</div>
        ${nested.length ? `<div style="display:flex;flex-direction:column;gap:2px;margin-top:2px">${nested.map(pluginRow).join('')}</div>` : ''}
        <div style="font-size:11px;color:#999;margin-top:6px">总开关一键开/关整个模块；下方每个功能一个总开关，不逐条列底层命令</div>
      </div>`;
      }
      const cTitle = c.title || (c.keywords && c.keywords.length ? c.keywords.join(' / ') : c.name);
      // 模块（随机图片 / 游戏娱乐 / 其他功能 / 测试功能）没有独立群黑白名单，不显示该行，避免误操作
      const grpBlock = (c.name === 'cmd_randomimg' || c.name === 'cmd_game' || c.name === 'cmd_other' || c.name === 'cmd_test') ? '' : `
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
// 服务商元数据（启动时从 /api/ai/providers 拉取，用于动态渲染下拉与默认值）
let AI_PROVIDERS = [];
let AI_PROVIDER_MAP = {};

async function loadAiProviders(){
  try{
    const d = await (await fetch('/api/ai/providers')).json();
    if(d && d.ok && Array.isArray(d.providers) && d.providers.length){
      AI_PROVIDERS = d.providers;
      AI_PROVIDER_MAP = {};
      d.providers.forEach(p => AI_PROVIDER_MAP[p.id] = p);
      const sel = document.getElementById('ai-provider');
      if(sel){
        const cur = sel.value;
        sel.innerHTML = AI_PROVIDERS.map(p=>
          `<option value="${p.id}">${p.name}${p.site ? '  ·  '+p.site : ''}</option>`
        ).join('');
        if(cur && AI_PROVIDER_MAP[cur]) sel.value = cur;
      }
    }
  }catch(e){}
}
function aiProviderHint(){
  const v = document.getElementById('ai-provider').value;
  const p = AI_PROVIDER_MAP[v];
  const el = document.getElementById('ai-provider-hint');
  if(!el) return;
  if(!p){ el.textContent = ''; return; }
  const fmt = {openai:'OpenAI 兼容', gemini:'Gemini 原生', anthropic:'Anthropic 原生'}[p.chat_format] || p.chat_format;
  const auth = {bearer:'Bearer 头', 'x-api-key':'x-api-key 头', query:'URL 参数'}[p.auth_type] || p.auth_type;
  el.textContent = `接口格式：${fmt}　认证：${auth}　默认模型：${p.default_model || '（需自填）'}`;
}
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
// 数字回填：0 是合法值，不能用 || 兜底（否则「记忆间隔=0（关闭）」会被错误显示成 5）
function setNum(id, v, dflt){
  const el = document.getElementById(id);
  if(!el) return;
  el.value = (v === 0 || v) ? v : (dflt !== undefined ? dflt : '');
}
async function loadAi(){
  try{
    if(!AI_PROVIDERS.length) await loadAiProviders();
    const d = await (await fetch('/api/ai/config')).json();
    document.getElementById('ai-enabled').checked = !!d.enabled;
    document.getElementById('ai-provider').value = d.provider || 'deepseek';
    document.getElementById('ai-key').value = d.api_key || '';
    document.getElementById('ai-base').value = d.base_url || '';
    document.getElementById('ai-model').value = d.model || '';
    document.getElementById('ai-preset').value = d.system_preset || '';
    setNum('ai-history', d.max_history, 12);
    setNum('ai-interval', d.memory_interval, 0);
    setNum('ai-temp', d.temperature, 0.85);
    setNum('ai-maxtokens', d.max_tokens, 0);
    setNum('ai-timeout', d.timeout, 90);
    document.getElementById('ai-enable-label').textContent = d.enabled ? '● 已启用（@机器人 或私聊触发）' : '○ 未启用';
    aiProviderHint();
  }catch(e){}
}
function sceneProvider(){
  const v = document.getElementById('ai-provider').value;
  const p = AI_PROVIDER_MAP[v];
  // 切换服务商时同步带出该服务商的默认地址与模型（用户仍可手改）
  if(p){
    document.getElementById('ai-base').value = p.default_base || '';
    if(p.default_model) document.getElementById('ai-model').value = p.default_model;
  }
  aiProviderHint();
  document.getElementById('ai-model-list').innerHTML = '';
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
    memory_interval: parseInt(document.getElementById('ai-interval').value)||0,
    temperature: parseFloat(document.getElementById('ai-temp').value)||0.85,
    max_tokens: parseInt(document.getElementById('ai-maxtokens').value)||0,
    timeout: parseInt(document.getElementById('ai-timeout').value)||90
  };
  const d = await (await fetch('/api/ai/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('ai-feedback').textContent = d.ok ? '✓ 已保存' : '保存失败';
  document.getElementById('ai-enable-label').textContent = body.enabled ? '● 已启用' : '○ 未启用';
  if(d.ok && d.config){ document.getElementById('ai-base').value = d.config.base_url || ''; document.getElementById('ai-model').value = d.config.model || ''; }
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
    sel.innerHTML = d.models.map(m=>{
      const id = (typeof m === 'string') ? m : (m.id || '');
      const nm = (typeof m === 'string') ? m : (m.name || m.id || '');
      return `<option value="${id}">${nm}${nm === id ? '' : '  ('+id+')'}</option>`;
    }).join('');
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
// ================= 管理分页：卡牌 / 钓鱼 / 用户数据 =================
const RARITY_CN = {common:'常见', fine:'优良', rare:'稀有', epic:'史诗', legend:'传说'};
const MAT_CN = {background:'背景', frame:'边框', seal:'卡封', back:'牌背', glow:'边框特效'};
const nz = v => (v===undefined||v===null ? '' : v);
const num0 = v => (v===undefined||v===null ? 0 : v);
let _tabLoaded = {};
function showTab(name){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.tab===name));
  document.querySelectorAll('.tab-panel').forEach(p=>{ p.style.display = (p.id==='tab-'+name)?'':'none'; });
  if(!_tabLoaded[name]){
    _tabLoaded[name] = true;
    if(name==='cards') loadCardsAdmin();
    else if(name==='fishing') loadFishingAdmin();
    else if(name==='users') loadUsers();
  }
}
async function post(url, body){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  return r.json();
}
function fb(id, txt, ok){
  const el = document.getElementById(id);
  if(el) el.textContent = (ok===false?'✗ ':'✓ ') + txt;
}
function fmtBonus(d){
  return Object.entries(d||{}).map(([k,v])=>k+':'+v).join(' ');
}
function parseBonus(s){
  const out = {};
  String(s||'').split(/[,，\s]+/).forEach(t=>{
    const [k,v] = t.split(':');
    if(k && v!==undefined && !isNaN(parseFloat(v))) out[k.trim()] = parseFloat(v);
  });
  return out;
}

// ---------- 卡牌管理 ----------
async function loadCardsAdmin(){
  try{
    const d = await (await fetch('/api/admin/cards')).json();
    document.getElementById('card-make-cost').value = d.make_cost;
    document.getElementById('card-use-fee').value = d.use_fee;
    document.getElementById('card-cooldown').value = d.cooldown;
    document.getElementById('card-tax').value = d.tax;
    const mp = d.material_prices || {};
    document.getElementById('card-material-prices').innerHTML =
      Object.keys(MAT_CN).map(k=>`
        <div><label>${MAT_CN[k]}（喵币/个）</label>
        <input type="number" id="mat-${k}" min="0" step="10" value="${num0(mp[k])}"></div>`).join('');
    const rr = d.rarity_ranges || {};
    document.getElementById('card-rarity-ranges').innerHTML =
      Object.keys(rr).map(r=>`
        <div><label>${r} 品级价格区间（最低 ~ 最高）</label>
        <div style="display:flex;gap:6px">
          <input type="number" id="rr-${r}-min" min="0" step="50" value="${rr[r][0]}">
          <input type="number" id="rr-${r}-max" min="0" step="50" value="${rr[r][1]}">
        </div></div>`).join('');
    const mats = d.materials || {};
    document.getElementById('card-materials-info').innerHTML =
      '<div class="ad-grid">' + Object.keys(mats).map(k=>{
        const m = mats[k];
        return `<div><label>${m.cn}</label><div style="font-size:18px;font-weight:700;color:#2563eb">${m.count} 种</div></div>`;
      }).join('') + '</div>';
  }catch(e){
    document.getElementById('card-materials-info').textContent = '加载失败: ' + e;
  }
}
async function saveCardsBase(){
  try{
    const d = await post('/api/admin/cards', {
      make_cost: parseInt(document.getElementById('card-make-cost').value)||0,
      use_fee: parseInt(document.getElementById('card-use-fee').value)||0,
      cooldown: parseInt(document.getElementById('card-cooldown').value)||0,
      tax: parseFloat(document.getElementById('card-tax').value)||0
    });
    fb('card-base-fb', d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok) loadCardsAdmin();
  }catch(e){ fb('card-base-fb', e.message, false); }
}
async function saveCardsMaterials(){
  const prices = {};
  Object.keys(MAT_CN).forEach(k=>prices[k] = parseInt(document.getElementById('mat-'+k).value)||0);
  try{
    const d = await post('/api/admin/cards', {material_prices: prices});
    fb('card-mat-fb', d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok) loadCardsAdmin();
  }catch(e){ fb('card-mat-fb', e.message, false); }
}
async function saveCardsRarity(){
  const ranges = {};
  document.querySelectorAll('#card-rarity-ranges [id^="rr-"]').forEach(inp=>{
    const m = inp.id.match(/^rr-(.+)-(min|max)$/);
    if(!m) return;
    ranges[m[1]] = ranges[m[1]] || [];
    ranges[m[1]][m[2]==='min'?0:1] = parseInt(inp.value)||0;
  });
  try{
    const d = await post('/api/admin/cards', {rarity_ranges: ranges});
    fb('card-rarity-fb', d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok) loadCardsAdmin();
  }catch(e){ fb('card-rarity-fb', e.message, false); }
}

// ---------- 钓鱼管理 ----------
let _fishCfg = null, _fishData = {}, _exData = {}, _gearData = {}, _gamData = null;
const ECON_FIELDS = [
  ['rod_cd','抛竿冷却（秒）'], ['gacha_cost','扭蛋单抽费用'], ['jackpot_reward','集齐全部鱼种大奖'],
  ['enchant_cost','附魔费用'], ['unenchant_cost','洗附魔费用'],
  ['market_tax','鱼市税率（0.1=10%）'], ['exchange_tax','交易所税率（0.05=5%）'],
  ['exchange_limit','交易所持仓上限（份）'], ['redpack_min','红包最低金额'], ['redpack_max_count','红包最多份数'],
];
const SOC_FIELDS = [
  ['steal_cooldown','偷鱼冷却（秒）'], ['steal_rate','偷鱼成功率（0.35=35%）'],
  ['electric_cost','电鱼电费'], ['electric_rate','电鱼成功率（0.65）'],
  ['electric_fine','电鱼失败罚款'], ['electric_cooldown','电鱼冷却（秒）'],
  ['aquarium_limit','基础鱼缸容量（买「鱼缸」后获得）'],
];
const AUTO_FIELDS = [
  ['cost','自动钓鱼启动费'],  // 间隔/成功率/最大时长已由「自动钓鱼机」等级决定（游戏内升级）
];
const GEAR_CFG = [
  {sec:'rods',   cn:'🎣 钓竿', keyL:'等级', keyIsLv:true,  num:[['price','价格']],                       dict:[],                  note:''},
  {sec:'hooks',  cn:'🪝 鱼钩', keyL:'等级', keyIsLv:true,  num:[['price','价格']],                       dict:[['bonus','稀有度加成']], note:'加成格式：rare:0.02 epic:0.02'},
  {sec:'lines',  cn:'🧵 鱼线', keyL:'等级', keyIsLv:true,  num:[['price','价格'],['mult','重量倍率']],   dict:[],                  note:''},
  {sec:'floats', cn:'🎈 鱼漂', keyL:'等级', keyIsLv:true,  num:[['price','价格'],['bonus','上钩率加成']], dict:[],                  note:''},
  {sec:'baits',  cn:'🪱 鱼饵', keyL:'ID',   keyIsLv:false, num:[['price','单价']],                       dict:[['bonus','稀有度加成']], note:'ID 如 bait1/bait2；加成格式：rare:0.02 epic:0.02'},
];
async function loadFishingAdmin(){
  try{
    const d = await (await fetch('/api/admin/fishing')).json();
    _fishCfg = d.config;
    _fishData = {}; _exData = {}; _gearData = {}; _gamData = null;
    renderEconomy(); renderGears(); renderFishTable(); renderGacha(); renderExchange(); renderSocialAuto(); renderGamble();
    document.getElementById('fish-count').textContent = Object.keys(_fishCfg.fish||{}).length;
    const ov = _fishCfg.overridden || [];
    document.querySelectorAll('.ad-btns .rst[data-sec]').forEach(b=>{
      const sec = b.getAttribute('data-sec');
      b.textContent = (sec && ov.includes(sec)) ? '恢复默认（已自定义）' : '恢复默认';
    });
  }catch(e){ document.getElementById('fish-economy').textContent = '加载失败: ' + e; }
}
function renderEconomy(){
  const e = _fishCfg.economy || {};
  document.getElementById('fish-economy').innerHTML =
    ECON_FIELDS.map(([k,cn])=>`
      <div><label>${cn}</label>
      <input type="number" step="any" id="econ-${k}" value="${nz(e[k])}"></div>`).join('');
}
function renderGears(){
  GEAR_CFG.forEach(g=>{ _gearData[g.sec] = JSON.parse(JSON.stringify(_fishCfg[g.sec]||{})); });
  document.getElementById('fish-gears').innerHTML = GEAR_CFG.map(g=>{
    const rows = Object.keys(_gearData[g.sec]).map(k=>gearRow(g,k));
    return `<div style="margin-bottom:14px;border:1px solid #e2e8f0;border-radius:10px;padding:10px">
      <div style="font-size:14px;font-weight:700;margin-bottom:6px">${g.cn}</div>
      <table class="ad-tb"><thead><tr><th>${g.keyL}</th><th>名称</th>
        ${g.num.map(f=>`<th>${f[1]}</th>`).join('')}
        ${g.dict.map(f=>`<th>${f[1]}</th>`).join('')}<th></th></tr></thead><tbody>
        ${rows.join('')}
      </tbody></table>
      ${g.note?`<div class="ad-note">${g.note}</div>`:''}
      <div class="ad-btns" style="margin-top:8px">
        <button onclick="gearAdd('${g.sec}')">➕ 新增</button>
        <button onclick="saveFishGears('${g.sec}')">保存</button>
        <button class="rst" data-sec="${g.sec}" onclick="resetFishSection('${g.sec}')">恢复默认</button>
        <span class="fb" id="fb-${g.sec}"></span>
      </div></div>`;
  }).join('');
  const ov = (_fishCfg.overridden||[]);
  document.querySelectorAll('#fish-gears .rst[data-sec]').forEach(b=>{
    b.textContent = ov.includes(b.getAttribute('data-sec')) ? '恢复默认（已自定义）' : '恢复默认';
  });
}
function gearRow(g,k){
  const rec = _gearData[g.sec][k];
  const fmt = fk => {
    const v = rec[fk];
    if(g.dict.some(x=>x[0]===fk)) return escapeHtml(typeof v==='object'?fmtBonus(v):nz(v));
    return escapeHtml(nz(v));
  };
  return `<tr>
    <td><input class="ad-id" data-k="${escapeHtml(k)}" value="${escapeHtml(k)}" onchange="gearKey(this,'${g.sec}')"></td>
    <td><input data-k="${escapeHtml(k)}" value="${escapeHtml(rec.name)}" onchange="gearVal(this,'${g.sec}','name')"></td>
    ${g.num.map(([fk])=>`<td><input type="number" step="any" data-k="${escapeHtml(k)}" value="${fmt(fk)}" onchange="gearVal(this,'${g.sec}','${fk}')"></td>`).join('')}
    ${g.dict.map(([fk])=>`<td><input data-k="${escapeHtml(k)}" value="${fmt(fk)}" onchange="gearVal(this,'${g.sec}','${fk}')"></td>`).join('')}
    <td><button onclick="gearDel(this,'${g.sec}')" style="background:#fee2e2;color:#b91c1c;border:none;border-radius:5px;padding:3px 8px;cursor:pointer">🗑</button></td>
  </tr>`;
}
function gearVal(inp, sec, field){
  const rec = _gearData[sec][inp.dataset.k];
  if(!rec) return;
  const isDict = GEAR_CFG.find(g=>g.sec===sec).dict.some(f=>f[0]===field);
  rec[field] = isDict ? parseBonus(inp.value) : (field==='name' ? inp.value : (parseFloat(inp.value)||0));
}
function gearKey(inp, sec){
  const old = inp.dataset.k, nk = (inp.value||'').trim();
  if(!nk || nk===old) return;
  if(_gearData[sec][nk]){ alert('已存在：'+nk); inp.value=old; return; }
  _gearData[sec][nk] = _gearData[sec][old];
  delete _gearData[sec][old];
  renderGears();
}
function gearDel(inp, sec){
  const k = inp.dataset.k;
  if(!confirm('删除该项？保存后生效')) return;
  delete _gearData[sec][k];
  renderGears();
}
function gearAdd(sec){
  const g = GEAR_CFG.find(x=>x.sec===sec);
  const ks = Object.keys(_gearData[sec]);
  const nums = ks.map(Number).filter(n=>!isNaN(n));
  let k = g.keyIsLv ? String((nums.length? Math.max(...nums) : 0)+1) : 'bait'+(ks.length+1);
  while(_gearData[sec][k]) k += 'x';
  const rec = {name:'新装备', price:0};
  g.num.forEach(([fk])=>{ rec[fk] = fk==='mult' ? 1 : 0; });
  g.dict.forEach(([fk])=>{ rec[fk] = {}; });
  _gearData[sec][k] = rec;
  renderGears();
}
async function saveFishGears(sec){
  try{
    const d = await post('/api/admin/fishing', {section: sec, data: _gearData[sec]});
    fb('fb-'+sec, d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-'+sec, e.message, false); }
}
function renderFishTable(){
  const box = document.getElementById('fish-table-wrap');
  if(!box) return;
  const sel = document.getElementById('fish-rarity-filter');
  if(sel.options.length===1){
    sel.innerHTML = '<option value="">全部</option>' + Object.keys(RARITY_CN).map(r=>`<option value="${r}">${RARITY_CN[r]}</option>`).join('');
  }
  if(!Object.keys(_fishData).length && _fishCfg) _fishData = JSON.parse(JSON.stringify(_fishCfg.fish||{}));
  const fish = _fishData;
  const q = (document.getElementById('fish-search').value||'').toLowerCase();
  const rf = sel.value;
  const ids = Object.keys(fish).filter(fid=>{
    const f = fish[fid]||{};
    if(rf && f.rarity!==rf) return false;
    if(q && !(fid.toLowerCase().includes(q) || (f.name||'').toLowerCase().includes(q))) return false;
    return true;
  }).sort();
  const rows = ids.map(fid=>{
    const f = fish[fid]||{};
    return `<tr>
      <td><input class="ad-id" data-fid="${escapeHtml(fid)}" value="${escapeHtml(fid)}" onchange="fishKey(this)"></td>
      <td><input data-fid="${escapeHtml(fid)}" value="${escapeHtml(f.name)}" onchange="fishVal(this,'name')"></td>
      <td><input data-fid="${escapeHtml(fid)}" value="${escapeHtml(f.emoji)}" style="width:60px" onchange="fishVal(this,'emoji')"></td>
      <td><select data-fid="${escapeHtml(fid)}" onchange="fishVal(this,'rarity')">${Object.keys(RARITY_CN).map(r=>`<option value="${r}" ${r===f.rarity?'selected':''}>${RARITY_CN[r]}</option>`).join('')}</select></td>
      <td><input type="number" data-fid="${escapeHtml(fid)}" value="${nz(f.base)}" onchange="fishVal(this,'base')"></td>
      <td><input type="number" data-fid="${escapeHtml(fid)}" value="${nz(f.wmin)}" onchange="fishVal(this,'wmin')"></td>
      <td><input type="number" data-fid="${escapeHtml(fid)}" value="${nz(f.wmax)}" onchange="fishVal(this,'wmax')"></td>
      <td><select data-fid="${escapeHtml(fid)}" onchange="fishVal(this,'zone')">
        <option value="day" ${f.zone==='day'?'selected':''}>昼</option>
        <option value="night" ${f.zone==='night'?'selected':''}>夜</option></select></td>
      <td><button onclick="fishDel(this)" style="background:#fee2e2;color:#b91c1c;border:none;border-radius:5px;padding:3px 8px;cursor:pointer">🗑</button></td>
    </tr>`;
  }).join('');
  box.innerHTML = `<table class="ad-tb"><thead><tr>
    <th style="width:150px">ID</th><th>名称</th><th style="width:60px">图标</th><th>稀有度</th>
    <th style="width:70px">基础价</th><th style="width:70px">最轻(g)</th><th style="width:70px">最重(g)</th><th>时段</th><th></th>
  </tr></thead><tbody>${rows}</tbody></table>`;
  document.getElementById('fish-count').textContent = Object.keys(fish).length;
}
function fishVal(inp, field){
  const f = _fishData[inp.dataset.fid];
  if(!f) return;
  f[field] = (field==='base'||field==='wmin'||field==='wmax') ? (parseInt(inp.value)||0) : inp.value;
}
function fishKey(inp){
  const old = inp.dataset.fid, nk = (inp.value||'').trim();
  if(!nk || nk===old) return;
  if(_fishData[nk]){ alert('ID 已存在：'+nk); inp.value=old; return; }
  _fishData[nk] = _fishData[old];
  delete _fishData[old];
  renderFishTable();
}
function fishDel(inp){
  const fid = inp.dataset.fid;
  if(!confirm('删除鱼种「'+((_fishData[fid]||{}).name||fid)+'」？保存后生效')) return;
  delete _fishData[fid];
  renderFishTable();
}
function addFishRow(){
  const fid = 'f_new_' + Date.now().toString(36);
  _fishData[fid] = {name:'新鱼', emoji:'🐟', rarity:'common', base:30, wmin:100, wmax:500, zone:'day'};
  renderFishTable();
}
function renderGacha(){
  const ch = (_fishCfg.gacha||{}).chance || {};
  document.getElementById('fish-gacha').innerHTML =
    Object.keys(RARITY_CN).map(r=>`
      <div><label>${RARITY_CN[r]} 权重</label>
      <input type="number" step="0.01" data-key="${r}" value="${nz(ch[r])}"></div>`).join('');
}
function renderExchange(){
  _exData = JSON.parse(JSON.stringify((_fishCfg.exchange)||{}));
  const keys = Object.keys(_exData).sort();
  const rows = keys.map(k=>{
    const g = _exData[k];
    return `<tr>
      <td><input class="ad-id" data-k="${escapeHtml(k)}" value="${escapeHtml(k)}" onchange="exKey(this)"></td>
      <td><input data-k="${escapeHtml(k)}" value="${escapeHtml(g.name)}" onchange="exVal(this,'name')"></td>
      <td><input type="number" data-k="${escapeHtml(k)}" value="${num0(g.price)}" onchange="exVal(this,'price')"></td>
      <td><input type="number" data-k="${escapeHtml(k)}" value="${num0(g.shelf)}" onchange="exVal(this,'shelf')"></td>
      <td><button onclick="exDel(this)" style="background:#fee2e2;color:#b91c1c;border:none;border-radius:5px;padding:3px 8px;cursor:pointer">🗑</button></td>
    </tr>`;
  }).join('');
  document.getElementById('fish-exchange').innerHTML =
    `<table class="ad-tb"><thead><tr><th>ID</th><th>名称</th><th>基础价</th><th>保质期(天)</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}
function exVal(inp, field){
  const g = _exData[inp.dataset.k];
  if(!g) return;
  g[field] = field==='name' ? inp.value : (parseInt(inp.value)||0);
}
function exKey(inp){
  const old = inp.dataset.k, nk = (inp.value||'').trim();
  if(!nk || nk===old) return;
  if(_exData[nk]){ alert('已存在：'+nk); inp.value=old; return; }
  _exData[nk] = _exData[old];
  delete _exData[old];
  renderExchange();
}
function exDel(inp){
  if(!confirm('删除该商品？保存后生效')) return;
  delete _exData[inp.dataset.k];
  renderExchange();
}
function exAdd(){
  let k = 'good' + (Object.keys(_exData).length+1);
  while(_exData[k]) k += 'x';
  _exData[k] = {name:'新商品', price:100, shelf:3};
  renderExchange();
}
function renderSocialAuto(){
  const s = (_fishCfg.social)||{}, a = (_fishCfg.auto)||{};
  document.getElementById('fish-social-auto').innerHTML =
    SOC_FIELDS.map(([k,cn])=>`<div><label>${cn}</label><input type="number" step="any" data-g="social" data-key="${k}" value="${nz(s[k])}"></div>`).join('') +
    AUTO_FIELDS.map(([k,cn])=>`<div><label>${cn}</label><input type="number" step="any" data-g="auto" data-key="${k}" value="${nz(a[k])}"></div>`).join('');
}
async function saveFishSocialAuto(){
  const social = {}, auto = {};
  document.querySelectorAll('#fish-social-auto [data-g]').forEach(inp=>{
    (inp.dataset.g==='social'?social:auto)[inp.dataset.key] = parseFloat(inp.value)||0;
  });
  try{
    let ok = true, msg = '已保存';
    for(const sec of ['social','auto']){
      const d = await post('/api/admin/fishing', {section: sec, data: sec==='social'?social:auto});
      if(!d.ok){ ok=false; msg=d.msg||'保存失败'; break; }
    }
    fb('fb-socialauto', msg, ok);
    if(ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-socialauto', e.message, false); }
}
async function resetFishSocialAuto(){
  if(!confirm('恢复社交/自动钓鱼为代码默认？')) return;
  try{
    let ok = true;
    for(const sec of ['social','auto']){
      const d = await post('/api/admin/fishing/reset', {section: sec});
      if(!d.ok) ok = false;
    }
    fb('fb-socialauto', ok?'已恢复默认':'部分失败', ok);
    if(ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-socialauto', e.message, false); }
}
function renderGamble(){
  if(_gamData===null){
    _gamData = JSON.parse(JSON.stringify((_fishCfg.gamble)||{}));
    _gamData.sicbo = _gamData.sicbo || {};
    _gamData.wheel = _gamData.wheel || {};
    _gamData.eraser = _gamData.eraser || [];
  }
  const s = _gamData.sicbo, w = _gamData.wheel;
  const pts = s.points || [1,2,12];
  document.getElementById('fish-gamble').innerHTML = `
    <div style="font-size:13px;font-weight:700;margin:8px 0 4px">骰宝赔率（填 1:x 的 x）</div>
    <div class="ad-grid">
      <div><label>豹子</label><input type="number" id="gam-baozi" value="${nz(s.baozi)}"></div>
      <div><label>大小单双</label><input type="number" id="gam-even" value="${nz(s.even)}"></div>
      <div><label>点数出现1次</label><input type="number" id="gam-p0" value="${num0(pts[0])}"></div>
      <div><label>点数出现2次</label><input type="number" id="gam-p1" value="${num0(pts[1])}"></div>
      <div><label>点数出现3次</label><input type="number" id="gam-p2" value="${num0(pts[2])}"></div>
    </div>
    <div style="font-size:13px;font-weight:700;margin:12px 0 4px">命运之轮</div>
    <div class="ad-grid">
      <div><label>最高层数</label><input type="number" id="gam-wmax" value="${nz(w.max)}"></div>
      <div><label>第1层成功率</label><input type="number" step="0.01" id="gam-base" value="${nz(w.base_rate)}"></div>
      <div><label>每层递减</label><input type="number" step="0.01" id="gam-step" value="${nz(w.rate_step)}"></div>
      <div><label>成功率下限</label><input type="number" step="0.01" id="gam-min" value="${nz(w.min_rate)}"></div>
      <div><label>奖金倍率基数</label><input type="number" step="0.01" id="gam-factor" value="${nz(w.factor)}"></div>
    </div>
    <div style="font-size:13px;font-weight:700;margin:12px 0 4px">擦弹倍率表（下限 ~ 上限 → 权重）</div>
    <table class="ad-tb" id="gam-eraser"><thead><tr><th>下限</th><th>上限</th><th>权重</th><th></th></tr></thead><tbody>${eraserRows()}</tbody></table>
    <div class="ad-btns" style="margin-top:8px"><button onclick="eraserAdd()">➕ 新增区间</button></div>`;
}
function eraserRows(){
  return (_gamData.eraser||[]).map((row,i)=>`<tr>
    <td><input type="number" step="any" value="${row[0]}" onchange="eraserSet(${i},0,this.value)"></td>
    <td><input type="number" step="any" value="${row[1]}" onchange="eraserSet(${i},1,this.value)"></td>
    <td><input type="number" step="any" value="${row[2]}" onchange="eraserSet(${i},2,this.value)"></td>
    <td><button onclick="eraserDel(${i})" style="background:#fee2e2;color:#b91c1c;border:none;border-radius:5px;padding:3px 8px;cursor:pointer">🗑</button></td></tr>`).join('');
}
function eraserSet(i,col,v){ _gamData.eraser[i][col] = parseFloat(v)||0; }
function eraserDel(i){ _gamData.eraser.splice(i,1); document.querySelector('#gam-eraser tbody').innerHTML = eraserRows(); }
function eraserAdd(){ _gamData.eraser.push([0,1,100]); document.querySelector('#gam-eraser tbody').innerHTML = eraserRows(); }
async function saveFishGamble(){
  const s = _gamData.sicbo, w = _gamData.wheel;
  s.baozi = parseInt(document.getElementById('gam-baozi').value)||1;
  s.even = parseInt(document.getElementById('gam-even').value)||1;
  s.points = [
    parseInt(document.getElementById('gam-p0').value)||1,
    parseInt(document.getElementById('gam-p1').value)||2,
    parseInt(document.getElementById('gam-p2').value)||12,
  ];
  w.max = parseInt(document.getElementById('gam-wmax').value)||1;
  w.base_rate = parseFloat(document.getElementById('gam-base').value)||0;
  w.rate_step = parseFloat(document.getElementById('gam-step').value)||0;
  w.min_rate = parseFloat(document.getElementById('gam-min').value)||0;
  w.factor = parseFloat(document.getElementById('gam-factor').value)||1;
  try{
    const d = await post('/api/admin/fishing', {section:'gamble', data:_gamData});
    fb('fb-gamble', d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-gamble', e.message, false); }
}
function collectSection(section){
  if(section==='economy'){
    const data = {};
    document.querySelectorAll('#fish-economy [id^="econ-"]').forEach(inp=>{
      data[inp.id.slice(5)] = parseFloat(inp.value)||0;
    });
    return data;
  }
  if(section==='fish') return _fishData;
  if(section==='gacha'){
    const chance = {};
    document.querySelectorAll('#fish-gacha [data-key]').forEach(inp=>chance[inp.dataset.key] = parseFloat(inp.value)||0);
    return {chance};
  }
  if(section==='exchange') return _exData;
  if(section==='gamble') return _gamData;
  if(section==='rods'||section==='hooks'||section==='lines'||section==='floats'||section==='baits') return _gearData[section];
  return {};
}
async function saveFishSection(section){
  const data = collectSection(section);
  try{
    const d = await post('/api/admin/fishing', {section, data});
    fb('fb-'+section, d.ok?'已保存':(d.msg||'保存失败'), d.ok);
    if(d.ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-'+section, e.message, false); }
}
async function resetFishSection(section){
  if(!confirm('恢复「'+section+'」为代码默认（你自定义的会清掉）？')) return;
  try{
    const d = await post('/api/admin/fishing/reset', {section});
    fb('fb-'+section, d.ok?'已恢复默认':(d.msg||'失败'), d.ok);
    if(d.ok){ _tabLoaded.fishing=false; showTab('fishing'); }
  }catch(e){ fb('fb-'+section, e.message, false); }
}

// ---------- 用户数据 ----------
async function loadUsers(){
  try{
    const d = await (await fetch('/api/admin/users')).json();
    const list = d.users || [];
    const box = document.getElementById('users-list');
    if(!list.length){ box.innerHTML = '<div class="usr-card">暂无用户数据</div>'; return; }
    box.innerHTML = list.map(u=>{
      const f = u.fishing||{}, c = u.cards||{}, e = u.exchange||{};
      const last = f.last_fish ? new Date(f.last_fish*1000).toLocaleString('zh-CN',{hour12:false}) : '从未';
      const oid = escapeHtml(u.openid);
      return `<div class="usr-card">
        <div class="usr-head">
          <div>
            <b>${escapeHtml(u.nick||'未命名')}</b>
            <span style="font-size:11px;color:#94a3b8;margin-left:6px">${oid}</span>
          </div>
          <div style="display:flex;gap:6px;align-items:center">
            <span style="font-size:12px;color:#666">喵币</span>
            <input type="number" id="bal-${oid}" style="width:110px;padding:4px 8px;border-radius:6px;border:1px solid #d1d5db" value="${u.balance}">
            <button onclick="saveUserBalance('${oid}')" style="background:#22c55e;color:#fff;border:none;padding:5px 12px;border-radius:6px;font-size:12px;cursor:pointer">保存余额</button>
            <button onclick="resetUserFishing('${oid}')" style="background:#ef4444;color:#fff;border:none;padding:5px 12px;border-radius:6px;font-size:12px;cursor:pointer">重置钓鱼</button>
          </div>
        </div>
        <div class="usr-meta">
          🎣 钓竿${f.rod} 鱼钩${f.hook} 鱼线${f.line} 鱼漂${f.float} ｜ 背包 ${f.inventory_count} 条 ｜ 图鉴 ${f.codex_count} 种 ｜ 累计钓 ${f.catches} 次 ｜ 卖鱼 ${f.sold_earn} 币 ｜ 扭蛋 ${f.gacha} 次 ｜ 成就 ${f.ach_count} ｜ ${f.auto_fish?'自动钓鱼中':'自动钓鱼关'} ｜ 最近 ${last}
        </div>
        <div class="usr-meta">
          🃏 卡牌 ${c.count} 张（市场中 ${c.market}）｜ 素材 ${c.materials} 个 ｜ 📈 交易所持仓 ${e.qty} 份（${e.kinds} 种）
        </div>
      </div>`;
    }).join('');
  }catch(e){ document.getElementById('users-list').textContent = '加载失败: ' + e; }
}
async function saveUserBalance(oid){
  const v = parseInt(document.getElementById('bal-'+oid).value)||0;
  const d = await post('/api/admin/users/balance', {openid: oid, balance: v});
  if(d.ok) document.getElementById('bal-'+oid).value = d.balance;
  alert(d.ok ? ('余额已设为 ' + d.balance + ' 喵币') : (d.msg||'保存失败'));
}
async function resetUserFishing(oid){
  if(!confirm('清空该用户的钓鱼数据（鱼获/图鉴/装备/成就）？此操作不可恢复！')) return;
  const d = await post('/api/admin/users/reset_fishing', {openid: oid});
  alert(d.ok ? '已重置' : (d.msg||'失败'));
  if(d.ok){ _tabLoaded.users=false; showTab('users'); }
}

loadAi(); loadBalance(); loadMem(); loadParsePlats(); loadBiliMode();
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""


async def start_webui(bot, port=WEBUI_PORT, tunnel=None):
    """启动 Web 后台，返回 (runner, site)。"""
    ui = WebUI(bot, tunnel=tunnel)
    runner = web.AppRunner(ui.app)
    await runner.setup()
    site = web.TCPSite(runner, WEBUI_BIND, port)
    await site.start()
    print(f"[UI] 后台已启动: {_display_host(WEBUI_BIND, port)}")
    if WEBUI_BIND not in ("127.0.0.1", "localhost"):
        print(f"     绑定地址 {WEBUI_BIND}（对外开放，建议在 settings.json 配置后台访问密码）")
    return runner, site