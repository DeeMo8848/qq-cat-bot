# -*- coding: utf-8 -*-
"""多平台解析网关（本 bot 适配版）。

职责：
- 加载 parse_config.json 配置 + 启用对应平台的 Parser
- 对收到的文本（含小程序卡片里挖出的 URL）做关键词+正则匹配
- 命中后调用对应 Parser 解析、渲染、经本 bot 的 Sender 发回群/私聊

本模块作为独立功能存在，不替换原有 B站解析。
"""
import asyncio
import re
from pathlib import Path

from config import ROOT
from ._log import logger
from .config import PluginConfig
from .debounce import Debouncer
from .download import Downloader
from .parsers import BaseParser
from .render import Renderer
from .sender import MessageSender

_PLUGIN_DIR = Path(__file__).parent
_CONFIG_FILE = Path(ROOT) / "tmp" / "parse" / "parse_config.json"


class ParseEngine:
    def __init__(self):
        self.cfg: PluginConfig | None = None
        self.renderer = None
        self.downloader = None
        self.debouncer = None
        self.sender = None
        self.parser_map: dict[str, BaseParser] = {}
        self.key_pattern_list: list[tuple[str, re.Pattern]] = []
        self._ready = False
        self._busy = False

    async def ensure_ready(self):
        if self._ready:
            return
        if self._busy:
            return
        self._busy = True
        try:
            self.cfg = PluginConfig(_CONFIG_FILE, _PLUGIN_DIR)
            try:
                await asyncio.to_thread(Renderer.load_resources)
            except Exception as e:
                logger.warning(f"[parse] 渲染资源加载失败: {e}")
            self.renderer = Renderer(self.cfg)
            self.downloader = Downloader(self.cfg)
            self.debouncer = Debouncer(self.cfg)
            self.sender = MessageSender(self.cfg, self.renderer)
            self._register_parser()
            self._ready = True
        finally:
            self._busy = False

    def _register_parser(self):
        self.parser_map.clear()
        enabled = set(self.cfg.parser.enabled_platforms())
        enabled_classes = [cls for cls in BaseParser.get_all_subclass()
                           if cls.platform.name in enabled]
        pats: list[tuple[str, re.Pattern]] = []
        for cls in enabled_classes:
            parser = cls(self.cfg, self.downloader)
            for kw, _ in cls._key_patterns:
                self.parser_map[kw] = parser
            for kw, pat in cls._key_patterns:
                pats.append((kw, re.compile(pat) if isinstance(pat, str) else pat))
        pats.sort(key=lambda x: -len(x[0]))
        self.key_pattern_list = pats
        logger.info("[parse] 启用平台: %s", "、".join(c.platform.name for c in enabled_classes))

    def candidates(self, ctx):
        """从一条消息里整理出所有候选文本（正文 + 小程序卡片URL + 引用内容）。"""
        cands = []
        msg = getattr(ctx, "message", None)
        text = (getattr(msg, "content", None) or "").strip()
        if text:
            cands.append(text)
        card = getattr(msg, "card_url", None)
        if card:
            cands.append(card)
        reply = getattr(msg, "raw_content", None) or ""
        reply = re.sub(r"<@[^>]*>", "", reply).strip()
        if reply and reply not in cands:
            cands.append(reply)
        return cands

    def match(self, cands):
        for cand in cands:
            low = cand.lower()
            for kw, pat in self.key_pattern_list:
                if kw.lower() not in low:
                    continue
                m = pat.search(cand)
                if m:
                    return kw, m, self.parser_map.get(kw)
        return None

    async def handle(self, ctx) -> bool:
        """尝试解析并回复；命中并尝试发送返回 True，否则返回 False。"""
        if not self._ready:
            try:
                await self.ensure_ready()
            except Exception as e:
                logger.warning(f"[parse] 引擎初始化失败: {e}")
                return False
        cands = self.candidates(ctx)
        if not cands:
            return False
        hit = self.match(cands)
        if not hit:
            return False
        kw, searched, parser = hit
        if parser is None:
            return False
        sender = getattr(ctx, "sender", None)
        if sender is None:
            return False
        try:
            result = await parser.parse(kw, searched)
        except Exception as e:
            logger.warning(f"[parse] 解析失败 {kw}: {e}")
            return False
        try:
            await self.sender.send_parse_result(ctx, sender, result)
        except Exception as e:
            logger.warning(f"[parse] 发送失败 {kw}: {e}")
            return False
        return True

    async def close(self):
        if self.downloader:
            try:
                await self.downloader.close()
            except Exception:
                pass


_engine = ParseEngine()


def engine() -> ParseEngine:
    return _engine


# 平台可读名（Web 后台展示用）
_PLATFORM_NAMES = {
    "bilibili": "B站",
    "douyin": "抖音",
    "kuaishou": "快手",
    "acfun": "A站",
    "mcmod": "MC百科",
    "ncm": "网易云",
    "steam": "Steam",
}


def list_parse_platforms() -> list[dict]:
    """返回各平台当前启用状态（供 Web 后台渲染开关）。"""
    items = []
    try:
        if _CONFIG_FILE.exists():
            cfg = PluginConfig(_CONFIG_FILE, _PLUGIN_DIR)
            sources = list(cfg.parser)
        else:
            # 配置文件尚未生成（首次运行前）时，用默认模板展示全部平台
            from .config import _DEFAULTS
            from .config import ParserConfig
            cfg = ParserConfig(_DEFAULTS.get("parsers_template") or [])
            sources = list(cfg)
        for p in sources:
            name = p.name
            items.append({
                "key": name,
                "name": _PLATFORM_NAMES.get(name, name),
                "enable": bool(getattr(p, "enable", False)),
            })
    except Exception as e:
        logger.warning(f"[parse] 读取平台状态失败: {e}")
    return items


def set_platform_enabled(name: str, enabled: bool) -> bool:
    """写入 parse_config.json 里某平台的 enable，并热重载引擎。

    用于 Web 后台逐平台开关：关掉 B站即可避免与原有 B站解析重复/冲突。
    """
    import json as _json
    if name not in _PLATFORM_NAMES:
        return False
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _CONFIG_FILE.exists():
            d = _json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        else:
            from .config import _DEFAULTS
            d = {k: ([dict(x) for x in v] if isinstance(v, list) else v)
                 for k, v in _DEFAULTS.items()}
        tpl = d.get("parsers_template") or []
        found = False
        for item in tpl:
            if item.get("__template_key") == name:
                item["enable"] = bool(enabled)
                found = True
                break
        if not found:
            tpl.append({"__template_key": name, "enable": bool(enabled),
                        "use_proxy": False, "cookies": ""})
            d["parsers_template"] = tpl
        _CONFIG_FILE.write_text(_json.dumps(d, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"[parse] 设置平台开关失败 {name}: {e}")
        return False


async def reload_platforms():
    """重新读取配置并注册对应平台（开关改动后调用，即时生效、无需重启 bot）。"""
    e = _engine
    e.cfg = PluginConfig(_CONFIG_FILE, _PLUGIN_DIR)
    e._register_parser()
    # 仅当引擎已完整初始化过才标记就绪；否则保持未就绪，首次解析时走 ensure_ready 全量初始化
    e._ready = (e.downloader is not None)
    logger.info("[parse] 已重载平台开关，启用: %s",
                "、".join(e.cfg.parser.enabled_platforms()) or "(无)")