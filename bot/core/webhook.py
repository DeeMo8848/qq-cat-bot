# -*- coding: utf-8 -*-
"""QQ 机器人 Webhook 接收服务。

官方在「开发 -> 回调配置」中勾选 GROUP_MESSAGE_CREATE 后，
会把群聊全量消息 POST 到本服务，从而实现「不 @ 也能自然语言触发」。

依赖：cryptography（用于回调验证的 ed25519 签名）
"""

import asyncio
import logging
import os
import re
import time

from aiohttp import web

from config import SECRET, WEBHOOK_PORT, BOT_ADMINS, BOT_ASSISTANTS, ROOT
from bot import commands
from bot.core import state
from plugins.meme import is_meme as meme_is_meme
from bot.core.sender import Sender

_log = logging.getLogger("webhook")

# 供 URL 上传临时存放待发送媒体文件的目录（经内网穿透暴露为公网地址）
MEDIA_DIR = os.path.join(ROOT, "tmp", "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# 同一 msg_id 重复回调的去重窗口（秒）：QQ 打包推送偶发同消息多次回调，窗口内去重
_DEDUP_WINDOW = 3.0


def _walk_elements(elements):
    """递归展开 msg_elements（含嵌套子元素，引用消息结构可能深一层）。"""
    out = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        out.append(el)
        out.extend(_walk_elements(el.get("msg_elements")))
    return out


def _collect_urls(d):
    """从消息里收集所有可能的图片 URL：顶层 attachments + msg_elements 内嵌套附件。"""
    urls = []
    for att in d.get("attachments") or []:
        u = att.get("url") if isinstance(att, dict) else getattr(att, "url", None)
        if u:
            urls.append(u)
    for el in _walk_elements(d.get("msg_elements") or []):
        for att in el.get("attachments") or []:
            u = att.get("url") if isinstance(att, dict) else getattr(att, "url", None)
            if u:
                urls.append(u)
    return urls


def _is_bili_url(url: str) -> bool:
    """URL 的域名是否为 bilibili 体系（避免取到中转/跳转地址）。"""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        for d in ("bilibili.com", "b23.tv", "bili22.cn", "bili23.cn",
                  "bili33.cn", "bili2233.cn", "bilivideo.com",
                  "bilivideo.cn", "bilivideo.net", "hdslb.com"):
            if host == d or host.endswith("." + d):
                return True
    except Exception:
        pass
    return False


def _extract_card_url(d):
    """从小程序/图文卡片（json/ark 元素）里挖出跳转 URL。

    QQ 分享小程序的链接常藏在卡片 JSON 的 meta 字段里。对齐
    astrbot_plugin_bili_resolver 的做法：遍历 meta 所有子项取 qqdocurl/url，
    并优先返回真正的 bilibili 域名链接。

    注意：官方 webhook 对 B站小程序卡片会剥掉原始 JSON（elements=None），只给标题与
    预览图、不携带视频链接，因此本函数对 B站卡片通常取不到东西——属平台限制，
    卡片消息不做视频获取/返回。
    """
    found: list[str] = []

    def _dig(content):
        if isinstance(content, dict):
            meta = content.get("meta")
            if isinstance(meta, dict):
                for _k, sub in meta.items():
                    if isinstance(sub, dict):
                        for key in ("qqdocurl", "jumpUrl", "url", "musicUrl", "videoUrl"):
                            val = sub.get(key)
                            if isinstance(val, str) and val.startswith("http"):
                                found.append(val)
            for v in content.values():
                _dig(v)
        elif isinstance(content, list):
            for v in content:
                _dig(v)

    for el in _walk_elements(d.get("msg_elements") or []):
        etype = el.get("type")
        content = el.get("content")
        if etype in ("json", "ark", "embed_channel", "video") and content:
            _dig(content)
    # 优先 bilibili 域名的真实链接
    for u in found:
        if _is_bili_url(u):
            return u
    return found[0] if found else None


class _WebhookMessage:
    """把 webhook 回调的消息 dict 包装成命令系统可用的轻量消息对象。"""

    def __init__(self, d):
        self.content = d.get("content", "")
        self.raw_content = d.get("content", "")  # 保留原始文本（含 <@openid> 标签，用于恢复 @ 的视觉顺序）
        self.id = d.get("id")
        self.group_openid = d.get("group_openid")
        self.user_openid = (d.get("author") or {}).get("user_openid")
        self.author = d.get("author", {})
        self.mentions = d.get("mentions") or []
        self.attachments = d.get("attachments") or []
        self.msg_elements = d.get("msg_elements") or []  # 原始元素树，供「引用图」等深度取图
        self.image_urls = _collect_urls(d)
        self.card_url = _extract_card_url(d)
        self.at_me = False  # 是否 @ 了机器人（AI 兜底触发依据），由事件类型/mentions 设置


def _ed25519_seed(bot_secret: str) -> bytes:
    """官方回调验证：用 botSecret 构造 ed25519 私钥种子（32 字节）。"""
    seed = bot_secret
    while len(seed) < 32:
        seed += seed
    return seed[:32].encode("utf-8")


def _sign(seed: bytes, msg: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(seed)
    return priv.sign(msg).hex()


class WebhookServer:
    def __init__(self, api, port: int = WEBHOOK_PORT):
        self.api = api
        self.sender = Sender(api)
        self.port = port
        self.app = web.Application()
        self.app.router.add_post("/", self.handle)
        self.app.router.add_get("/", self.ping)
        self.app.router.add_get("/media/{filename}", self.serve_media)
        # 同一 msg_id 的重复回调去重：保留最近处理过的 id 和时刻
        self._seen_msgs: dict[str, float] = {}

    async def ping(self, request):
        return web.Response(text="webhook ok")

    async def serve_media(self, request):
        """把 _media_tmp 里的临时媒体文件暴露成公网 URL，供官方上传接口拉取。"""
        filename = request.match_info["filename"]
        if "/" in filename or "\\" in filename or ".." in filename:
            return web.Response(status=400, text="bad name")
        path = os.path.join(MEDIA_DIR, filename)
        if not os.path.isfile(path):
            return web.Response(status=404, text="not found")
        return web.FileResponse(path)

    async def handle(self, request):
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")

        op = payload.get("op")
        if op == 13:  # 回调地址验证
            return await self._verify(payload)
        if op == 0:  # 事件分发
            asyncio.create_task(self._dispatch(payload))
            return web.Response(status=200, text="ok")
        return web.Response(status=200, text="ok")

    async def _verify(self, payload):
        d = payload.get("d", {})
        plain_token = d.get("plain_token", "")
        event_ts = d.get("event_ts", "")
        signature = _sign(_ed25519_seed(SECRET), (event_ts + plain_token).encode("utf-8"))
        return web.json_response({"plain_token": plain_token, "signature": signature})

    async def _dispatch(self, payload):
        t = payload.get("t", "")
        d = payload.get("d", {})
        # 消息重复回调去重：同一 msg_id 短时间内的重复推送直接丢弃，避免重复下载/发消息
        mid = d.get("id")
        if mid:
            now = time.time()
            if mid in self._seen_msgs and now - self._seen_msgs[mid] < _DEDUP_WINDOW:
                return
            if len(self._seen_msgs) > 1000:  # 防无限增长：定时清理过期记录
                self._seen_msgs = {
                    k: v for k, v in self._seen_msgs.items() if now - v < 60
                }
            self._seen_msgs[mid] = now
        print(f"[webhook] 收到事件: {t} | content={d.get('content')!r} | mentions={d.get('mentions')} | att={d.get('attachments')} | elements={d.get('msg_elements')} | scene={d.get('message_scene')}", flush=True)
        if t == "GROUP_MESSAGE_CREATE":
            # 全量消息：@机器人的文字内容也走这个通道，所以这里同时处理「@我」和「不带@」；
            # 只 @ 了别人 → 跳过。GROUP_AT 事件仅作兜底（去重后不会重复回复）。
            await self._on_group_message(d, at_event=False)
        elif t == "GROUP_AT_MESSAGE_CREATE":
            await self._on_group_message(d, at_event=True)
        elif t == "C2C_MESSAGE_CREATE":
            await self._on_c2c_message(d)

    async def _on_group_message(self, d, at_event=False):
        content = (d.get("content") or "").strip()
        group_openid = d.get("group_openid")
        msg_id = d.get("id")
        card_url = _extract_card_url(d)
        # 小程序/图文卡片分享常带空正文，此时靠从卡片 meta 挖出的 URL 继续处理
        if (not content and not card_url) or not group_openid or not msg_id:
            return

        state.ensure_recent_group(group_openid)

        mentions = d.get("mentions") or []
        at_me = any(m.get("is_you") for m in mentions if isinstance(m, dict))

        # 去掉消息里的 @ 标记，避免干扰命令匹配
        clean = re.sub(r"<@[0-9A-Fa-f]+>\s*", "", content).strip()
        if not clean and card_url:
            clean = card_url
        if not clean:
            return

        if mentions and not at_me:
            # 只 @ 了别人、没 @ 机器人：若是普通闲聊则跳过；
            # 但 meme 命令常把「@某人」当图片参数（如 鞭策@李四），此时要放行。
            if not (meme_is_meme(clean)):
                return

        # 群内任意用户都可触发（具体命令各自的权限仍由 commands 里的 role 决定，
        # 如「你好」仍仅限管理员）。若某命令需限人群，可在后台用群名单/后续的用户名单控制。

        # 交给统一命令分发（菜单 / 你好 / B站解析 等）
        msg = _WebhookMessage(d)
        msg.content = clean
        msg.at_me = bool(at_me) or bool(at_event)
        ctx = commands.CommandCtx(client=None, message=msg, sender=self.sender)
        try:
            await commands.dispatch(ctx)
        except Exception as e:
            _log.error("[webhook] 处理消息失败: %s", e)
            hint = _get_permission_hint(e)
            if hint:
                try:
                    await ctx.reply(hint)
                    _log.info("[webhook] 已用引用消息返回权限提示")
                except Exception as e2:
                    _log.error("[webhook] 权限提示发送失败: %s", e2)

    async def _on_c2c_message(self, d):
        content = (d.get("content") or "").strip()
        msg_id = d.get("id")
        if not content or not msg_id:
            return

        # 私聊是用户主动找机器人，不限制身份，权限由命令自身的 role 控制
        msg = _WebhookMessage(d)
        msg.at_me = True  # 私聊视为点名机器人
        ctx = commands.CommandCtx(client=None, message=msg, sender=self.sender)
        try:
            await commands.dispatch(ctx)
        except Exception as e:
            _log.error("[webhook] 处理 C2C 消息失败: %s", e)
            hint = _get_permission_hint(e)
            if hint:
                try:
                    await ctx.reply(hint)
                except Exception as e2:
                    _log.error("[webhook] C2C 权限提示发送失败: %s", e2)


def _get_permission_hint(exc) -> str | None:
    """主动消息被平台以「无权限」拒绝时，返回一句可引用式回复的提示。"""
    if "无权限" in str(exc):
        return "该群未开启机器人的发送权限，请联系群主/管理员开启「发送消息」权限后即可使用喵"


async def start_webhook(api, port: int = WEBHOOK_PORT):
    server = WebhookServer(api, port=port)
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner, site
