# -*- coding: utf-8 -*-
"""QQ 机器人 主入口。

运行方式（在项目目录下）：
    python main.py

启动后：
    1. 机器人连接官方 WebSocket 网关，保持在线并监听消息
    2. 本地 Web 后台启动，浏览器打开 http://127.0.0.1:8080 可查看状态/管理功能开关
"""

import asyncio
import sys
import time

import botpy

from bot import commands  # noqa: F401  确保命令模块加载
from bot.core import fonts as _fonts
from bot.core import vpn_bypass
from bot.core.sender import Sender
from bot.core.static_server import start_static
from bot.core.tunnel import TunnelManager
from bot.core.webhook import start_webhook
from bot.core.webui import start_webui
from config import (APPID, SECRET, DEBUG, WEBUI_PORT, WEBHOOK_PORT,
                    STATIC_PUBLIC_URL, TUNNEL_ENABLED)

# stdout 行缓冲：systemd 用 `StandardOutput=append:` 把输出重定向到文件时，
# Python 会判定 stdout 非 tty 而启用块缓冲（4KB/8KB），导致 logs/bot.log
# 迟迟不落盘、排障时看不到实时输出（2026-09-24 排 4009 故障时吃过这个亏）。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001 —— 个别环境不支持 reconfigure，忽略即可
    pass

# 修补 botpy 网关的重连缺陷：4009 死循环 + 心跳协程静默死亡。
# 必须在 MyBot 实例化之前生效，详见 bot/core/botpy_resilience.py。
from bot.core import botpy_resilience as _resilience  # noqa: E402

_resilience.install(verbose=DEBUG)

_LOG_LEVEL = "DEBUG" if DEBUG else "INFO"

# 把项目内置字体注入 Skia —— 供所有走「字体族名」的渲染（meme 等）使用，
# 避免服务器/新设备没装中文字体时渲染出「口口口口」。详见 bot/core/fonts.py。
_fonts.install(verbose=DEBUG)


class MyBot(botpy.Client):
    def __init__(self):
        # public_messages: 群聊@事件 + 单聊(C2C)消息事件
        # public_guild_messages: 频道内@机器人 事件
        intents = botpy.Intents(
            public_messages=True,
            public_guild_messages=True,
        )
        # timeout=30：默认 5s 太短，发视频/音频时 QQ 要从隧道拉取媒体文件，常超时导致 file_info 无效
        super().__init__(intents=intents, log_level=_LOG_LEVEL, timeout=30)
        self.sender = Sender(self.api)
        self.online = False
        self.last_ready = None

    # ---------- 连接就绪 ----------
    async def on_ready(self):
        self.online = True
        self.last_ready = time.strftime("%Y-%m-%d %H:%M:%S")
        robot = self.robot
        print("=" * 50)
        print("  [OK] 机器人已上线")
        print(f"   ID  : {robot.id}")
        print(f"   名字: {robot.name}")
        print(f"   后台: http://127.0.0.1:{WEBUI_PORT}")
        print("   现在可以在 QQ 里 @它 发「菜单」或「你好」测试了。")
        print("=" * 50)

        # 启动/重启完成提醒：默认发给「触发重启的那个会话」。
        # 由 bot/core/boot_notify.py 按 state.json 的 boot_notify 模式决定发不发、发给谁。
        # 整个流程失败都不应影响 bot 主流程，因此全包在 try 里。
        try:
            from bot.core import boot_notify
            await boot_notify.send_boot_notify(self.sender, self.api)
        except Exception as e:
            print(f"[ops] 启动提醒发送失败（不影响运行）: {type(e).__name__}: {e}",
                  flush=True)

    # ---------- 消息事件 ----------
    async def on_group_at_message_create(self, message):   # 群聊里被 @
        print(f"[ws] 收到群@消息: content={message.content!r}", flush=True)
        await self._dispatch(message)

    async def on_c2c_message_create(self, message):        # 用户私聊机器人
        print(f"[ws] 收到私聊消息: content={message.content!r}", flush=True)
        await self._dispatch(message)

    async def on_at_message_create(self, message):         # 频道里被 @
        await self._dispatch(message)

    # ---------- 统一分发 ----------
    async def _dispatch(self, message):
        # WebSocket 通道收到的都是「@机器人 / 私聊机器人」消息，视为点名了机器人
        setattr(message, "at_me", True)
        ctx = commands.CommandCtx(client=self, message=message, sender=self.sender)
        try:
            await commands.dispatch(ctx)
        except Exception as e:  # 命令报错不要让整个进程崩溃
            print(f"[错误] 处理消息失败: {type(e).__name__}: {e}")
            try:
                await ctx.reply("呜，我出错了，稍后再试～")
            except Exception:
                pass


async def run_bot_forever(bot):
    """启动机器人；连接失败（如 IP 白名单未配置）则每 30 秒自动重试，不退出。"""
    while True:
        try:
            await bot.start(appid=APPID, secret=SECRET)
            break  # 正常退出（进程被关闭）
        except Exception as e:
            print(f"[错误] 机器人连接失败: {type(e).__name__}: {e}")
            print("       30 秒后自动重试。若为 IP 白名单问题，请先在开放平台")
            print("       把当前公网 IP 加入白名单（后台页面会显示当前 IP）。")
            await asyncio.sleep(30)


async def main():
    bot = MyBot()

    # 内网穿透（默认关闭）。生产环境用「A 记录 + 宝塔 nginx 反代」直接暴露
    # 9091/9092，无需隧道；仅在「无公网 IP」或「想隐藏源站 IP」时才在
    # settings.json 里把 TUNNEL_ENABLED 设为 true。详见 deploy/SERVER.md 第五节。
    tunnel = TunnelManager(WEBHOOK_PORT)
    if TUNNEL_ENABLED:
        tunnel.start()
    else:
        print("[隧道] 已禁用（TUNNEL_ENABLED=false）—— 公网入口由 nginx 反代提供")

    # 保障「本机翻墙」与「隧道」共存：把 cloudflared 出站流量在代理内核里固定为直连
    # （否则 VPN 的 TUN 模式会把隧道流量丢给代理节点，bot 会在线的同时收不到消息）
    vpn_bypass.start_auto()

    # 启动本地 Web 后台（与机器人同进程、同事件循环，始终可访问）
    runner, site = await start_webui(bot, port=WEBUI_PORT, tunnel=tunnel)

    # 启动 Webhook 接收服务（接收群聊全量消息，实现「不 @ 自然语言」触发）
    wh_runner, wh_site = await start_webhook(bot.api, port=WEBHOOK_PORT)
    print(f"  [OK] Webhook 服务已启动: http://127.0.0.1:{WEBHOOK_PORT}（需配合内网穿透使用）")

    # 启动静态页面服务（仅开放 bot/public_html/，供公网链接访问）
    st_runner, st_site = await start_static()
    print(f"  [OK] 静态页面服务已启动，公网地址: {STATIC_PUBLIC_URL}")

    try:
        # 启动机器人（失败自动重试，Web 后台保持运行）
        await run_bot_forever(bot)
    finally:
        await st_runner.cleanup()
        await wh_runner.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())