# -*- coding: utf-8 -*-
"""QQ 机器人 主入口。

运行方式（在项目目录下）：
    python main.py

启动后：
    1. 机器人连接官方 WebSocket 网关，保持在线并监听消息
    2. 本地 Web 后台启动，浏览器打开 http://127.0.0.1:8080 可查看状态/管理功能开关
"""

import asyncio
import time

import botpy

from bot import commands  # noqa: F401  确保命令模块加载
from bot.core.sender import Sender
from bot.core.tunnel import TunnelManager
from bot.core.webhook import start_webhook
from bot.core.webui import start_webui
from config import APPID, SECRET, DEBUG, WEBUI_PORT, WEBHOOK_PORT

_LOG_LEVEL = "DEBUG" if DEBUG else "INFO"


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

    # 启动 cloudflared 内网穿透（自动，非阻塞），供开放平台回调使用
    tunnel = TunnelManager(WEBHOOK_PORT)
    tunnel.start()

    # 启动本地 Web 后台（与机器人同进程、同事件循环，始终可访问）
    runner, site = await start_webui(bot, port=WEBUI_PORT, tunnel=tunnel)

    # 启动 Webhook 接收服务（接收群聊全量消息，实现「不 @ 自然语言」触发）
    wh_runner, wh_site = await start_webhook(bot.api, port=WEBHOOK_PORT)
    print(f"  [OK] Webhook 服务已启动: http://127.0.0.1:{WEBHOOK_PORT}（需配合内网穿透使用）")

    try:
        # 启动机器人（失败自动重试，Web 后台保持运行）
        await run_bot_forever(bot)
    finally:
        await wh_runner.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())