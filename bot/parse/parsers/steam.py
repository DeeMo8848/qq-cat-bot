# -*- coding: utf-8 -*-
"""Steam 商店解析器（参考 astrbot_plugin_SteamLink 移植）。

支持两种触发：
- 自动识别 Steam 商店链接 store.steampowered.com/app/{appid}
- 命令「steamid {appid}」查询（原名 /查找、/steam 已按要求移除）

返回封面图 + 基本信息文本。
"""
from typing import ClassVar

from aiohttp import ClientError

from .._log import logger
from ..config import PluginConfig
from ..data import ParseResult, Platform
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle

_API = "https://store.steampowered.com/api/appdetails"


class SteamParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="steam", display_name="Steam")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.headers.update({"Accept-Language": "zh-CN,zh;q=0.8,en;q=0.5"})

    @handle(
        "store.steampowered.com/app",
        r"https?://store\.steampowered\.com/app/(?P<appid>\d+)",
    )
    async def _parse_store_url(self, searched):
        return await self._build_result(searched.group("appid"))

    @handle("steamid", r"(?i)steamid\s+(?P<appid>\d+)")
    async def _parse_cmd(self, searched):
        return await self._build_result(searched.group("appid"))

    async def _fetch(self, appid: int, lang: str) -> dict:
        params = {"appids": str(appid), "l": lang}
        try:
            async with self.session.get(
                _API, params=params, headers=self.headers, proxy=self.proxy
            ) as resp:
                if resp.status != 200:
                    raise ParseException(f"Steam API HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except (ClientError, ValueError) as e:
            raise ParseException(f"Steam API 请求失败: {e}")
        node = (payload or {}).get(str(appid)) or {}
        if not node.get("success"):
            raise ParseException("未找到该 AppID 的游戏信息")
        return node.get("data") or {}

    async def _build_result(self, appid: str) -> ParseResult:
        appid = str(appid).strip()
        # 优先简中 → 繁中 → 英文
        data: dict = {}
        for lang in ("schinese", "tchinese", "english"):
            try:
                data = await self._fetch(int(appid), lang)
            except ParseException:
                continue
            if data:
                break
        if not data:
            raise ParseException("未找到该 AppID 的游戏信息")

        name = data.get("name") or ""
        store_url = f"https://store.steampowered.com/app/{appid}"

        def _texts(key):
            vals = data.get(key) or []
            return "、".join(str(x) for x in vals if str(x).strip()) if isinstance(vals, list) else ""

        genres = "、".join(
            g["description"].strip()
            for g in (data.get("genres") or [])
            if isinstance(g, dict) and g.get("description")
        )

        lines = []
        if name:
            lines.append(f"🎮 {name}")
        lines.append(f"🆔 AppID: {appid}")
        if data.get("type"):
            lines.append(f"📦 类型: {data['type']}")
        if genres:
            lines.append(f"🏷️ 分类: {genres}")
        dev = _texts("developers")
        pub = _texts("publishers")
        if dev:
            lines.append(f"👨‍💻 开发商: {dev}")
        if pub:
            lines.append(f"🏢 发行商: {pub}")
        rd = data.get("release_date") or {}
        if rd.get("date"):
            date = str(rd["date"]).strip()
            lines.append(f"📅 发售: {date}" + ("（未发售）" if rd.get("coming_soon") else ""))
        sd = (data.get("short_description") or "").strip()
        if sd:
            lines.append(f"📝 简介: {sd}")
        lines.append(f"🔗 {store_url}")
        text = "\n".join(lines).strip()

        author = self.create_author(name or appid)
        if data.get("header_image"):
            return self.result(
                title=name or appid,
                text=text,
                url=store_url,
                author=author,
                contents=[self.create_graphics_content(data["header_image"], text)],
            )
        # 无封面则纯文本
        return self.result(
            title=name or appid,
            text=text,
            url=store_url,
            author=author,
        )