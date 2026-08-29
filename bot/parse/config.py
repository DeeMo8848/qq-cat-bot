from __future__ import annotations

import json
import zoneinfo
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints
from ._log import logger


class ConfigNode:
    """配置节点, 把 dict 变成强类型对象。"""

    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}
    _FIELDS_CACHE: dict[type, set[str]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _fields(cls) -> set[str]:
        return cls._FIELDS_CACHE.setdefault(
            cls,
            {k for k in cls._schema() if not k.startswith("_")},
        )

    @staticmethod
    def _is_optional(tp: type) -> bool:
        if get_origin(tp) in (Union, UnionType):
            return type(None) in get_args(tp)
        return False

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_children", {})

    def __getattr__(self, key: str) -> Any:
        if key in self._fields():
            value = self._data.get(key)
            tp = self._schema().get(key)
            if isinstance(tp, type) and issubclass(tp, ConfigNode):
                children: dict[str, ConfigNode] = self.__dict__["_children"]
                if key not in children:
                    children[key] = tp(value)
                return children[key]
            return value
        if key in self.__dict__:
            return self.__dict__[key]
        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._fields():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)

    def raw_data(self) -> Mapping[str, Any]:
        return MappingProxyType(self._data)


class ConfigNodeContainer:
    """把 list 的 dict 变成 dict 的对象集合。"""

    def __init__(self, nodes: list[dict[str, Any]], item_cls: type[ConfigNode], key_name="__template_key"):
        self._nodes: dict[str, ConfigNode] = {}
        for node in nodes:
            key = node.get(key_name)
            if not key:
                continue
            self._nodes[key] = item_cls(node)

    def __getattr__(self, name: str) -> ConfigNode:
        if name in self._nodes:
            return self._nodes[name]
        raise AttributeError(name)

    def __iter__(self):
        return iter(self._nodes.values())

    def keys(self):
        return self._nodes.keys()

    def items(self):
        return self._nodes.items()


class ParserItem(ConfigNode):
    enable: bool
    use_proxy: bool
    cookies: str | None
    show_body_text: bool | None
    video_send_mode: str | None
    video_codecs: str | None
    video_codec_list: list | None
    video_quality: str | None
    nsfw: str | None
    max_page: int | None

    @property
    def name(self) -> str:
        return self._data.get("__template_key")


class ParserConfig(ConfigNodeContainer):
    acfun: ParserItem
    bilibili: ParserItem
    douyin: ParserItem
    kuaishou: ParserItem
    ncm: ParserItem
    steam: ParserItem

    def __init__(self, nodes: list[dict[str, Any]]):
        super().__init__(nodes, item_cls=ParserItem)

    def platforms(self) -> list[str]:
        return list(self._nodes.keys())

    def enabled_platforms(self) -> list[str]:
        return [k for k, v in self._nodes.items() if bool(getattr(v, "enable", False))]


# 默认配置（首次运行写入 parse_config.json）
_DEFAULTS = {
    "whitelist": [],
    "blacklist": [],
    "require_at_in_group": False,
    "debounce_interval": 30,
    "source_max_size": 200,      # MB
    "source_max_minute": 10,     # 分钟
    "audio_to_file": True,
    "single_heavy_render_card": True,
    "forward_threshold": 6,
    "show_download_fail_tip": True,
    "download_timeout": 90,
    "download_retry_times": 2,
    "common_timeout": 15,
    "proxy": "",
    "parsers_template": [
        {"__template_key": "bilibili", "enable": True, "use_proxy": False, "cookies": "",
         "video_codecs": "AVC", "video_quality": "_720P"},
        {"__template_key": "douyin", "enable": True, "use_proxy": False, "cookies": ""},
        {"__template_key": "kuaishou", "enable": True, "use_proxy": False, "cookies": ""},
        {"__template_key": "acfun", "enable": True, "use_proxy": False, "cookies": ""},
        {"__template_key": "ncm", "enable": True, "use_proxy": False, "cookies": ""},
        {"__template_key": "steam", "enable": True, "use_proxy": False, "cookies": ""},
    ],
}


class PluginConfig(ConfigNode):
    whitelist: list[str]
    blacklist: list[str]
    require_at_in_group: bool
    arbiter: bool
    debounce_interval: int
    source_max_size: int
    source_max_minute: int
    audio_to_file: bool
    single_heavy_render_card: bool
    forward_threshold: int
    show_download_fail_tip: bool
    download_timeout: int
    download_retry_times: int
    common_timeout: int
    proxy: str | None
    parsers_template: list[dict[str, Any]]

    def __init__(self, config_file: Path, plugin_dir: Path):
        self._config_file = config_file
        data = _load_or_default(config_file)
        super().__init__(data)

        self.extra_fields = {}

        # 派生字段
        self.proxy = self.proxy or None
        self.max_duration = self.source_max_minute * 60
        self.max_size = self.source_max_size * 1024 * 1024
        self.timezone = zoneinfo.ZoneInfo("Asia/Shanghai")
        self.emoji_cdn = "https://cdn.jsdelivr.net/npm/emoji-datasource-facebook@14.0.0/img/facebook/64/"
        self.emoji_style = "FACEBOOK"

        # 路径（缓存放项目 tmp 下，与其他模块一致：重启即清，脱离项目体积）
        project_root = Path(__file__).resolve().parents[2]
        self.data_dir = project_root / "tmp" / "parse"
        self.plugin_dir = plugin_dir
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_dir = self.data_dir / "cookies"
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.default_template_file = plugin_dir / "default_template.json"

        # Parser
        self.parser = ParserConfig(self.parsers_template or [])

    def save_config(self) -> None:
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            d = dict(self.raw_data())
            d["parsers_template"] = [dict(n.raw_data()) for n in self.parser]
            self._config_file.write_text(
                json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[parse] 保存配置失败: {e}")

    def add_blacklist(self, umo: str):
        if umo not in self.blacklist:
            self.blacklist.append(umo)
            self.save_config()

    def remove_blacklist(self, umo: str):
        if umo in self.blacklist:
            self.blacklist.remove(umo)
            self.save_config()


def _load_or_default(file: Path) -> dict:
    if file.exists():
        try:
            d = dict(json.loads(file.read_text(encoding="utf-8")))
            for k, v in _DEFAULTS.items():
                d.setdefault(k, v)
            # 迁移：parsers_template 里补齐默认平台（保留旧的开关状态，新平台取默认 enable）
            _merge_missing_platforms(d)
            return d
        except Exception:
            pass
    return {k: (_DEFAULT_LIST(v) if isinstance(v, list) else v) for k, v in _DEFAULTS.items()}


def _merge_missing_platforms(d: dict) -> None:
    """把 _DEFAULTS 里、但文件 parsers_template 中缺失的平台补进去（如新增 Steam）。"""
    existed = {x.get("__template_key") for x in (d.get("parsers_template") or []) if isinstance(x, dict)}
    missing = [p for p in _DEFAULTS.get("parsers_template", [])
               if isinstance(p, dict) and p.get("__template_key") not in existed]
    if missing:
        tpl = d.get("parsers_template") or []
        tpl.extend(_dict_copy(p) for p in missing)
        d["parsers_template"] = tpl


def _DEFAULT_LIST(v):
    return [_dict_copy(x) for x in v]


def _dict_copy(x):
    return dict(x)