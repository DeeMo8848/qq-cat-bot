# -*- coding: utf-8 -*-
"""影之诗制卡器 bot 插件 —— 命令文本解析。

按需求实现的解析规则：

* 触发词须在消息开头（其后紧跟分隔符或结束），先剥离；
* 参数之间可用 **空格 / ，/ , / * / . / - / 、/ | / /** 等任意组合分隔；
* 参数顺序无关；
* **无法识别的片段直接丢弃**（如「暴击率99」）；
* 数字类参数带前缀（`费10` / `费用5` / `攻5` / `攻击10` / `体10` / `体力6`）；
* 卡名用「卡名」前缀粘连（`卡名卓越创造物Ω`）；
* 职业等枚举关键词直接裸写（`超越者`、`皇家`）；
* 非「随从」时攻击/体力不生效。
"""

import re

from . import config_schema as CS

# 分隔符：空白 + 常见中英文标点。
# 注意「-」和「.」按需求也算分隔符（卡名内的连字符会被切开，属已知取舍）。
_SPLIT_RE = re.compile(r"[\s，,、;；*\.\-—_/\\|·]+")

# 数字类参数前缀：长的排前面，避免「费用10」被「费」截成「用10」
_NUM_PREFIXES = [
    ("费用", "cost"),
    ("攻击", "atk"),
    ("体力", "life"),
    ("生命", "life"),
    ("攻击力", "atk"),
    ("防御", "life"),
    ("费", "cost"),
    ("攻", "atk"),
    ("体", "life"),
]

# 卡名前缀（长的排前面）
_NAME_PREFIXES = ("卡名", "卡牌名", "名字", "名称")

# 文本类参数前缀（效果图用）
_TEXT_PREFIXES = [
    ("超进化", "super_evolve"),
    ("进化", "evolve"),
    ("正文", "detail1"),
    ("效果", "detail1"),
    ("画师", "illustrator"),
    ("脚注", "diy"),
    ("作者", "diy"),
]

# 数值合法性：纯数字，允许结尾一个「+」（原工具支持 "10+"）
_NUM_OK = re.compile(r"^\d{1,3}\+?$")


def strip_triggers(text: str, triggers) -> str:
    """剥掉【开头】命中的触发词（长的优先，避免「sv卡牌」抢了「sv卡牌效果图」）。

    ★ 只在开头剥离：触发词出现在句子中间（闲聊里提到「sv卡牌」）不算触发，
    与 commands 侧 `_match_trigger` 的判据保持一致。
    """
    t = text or ""
    stripped = t.lstrip()
    for tr in sorted(triggers, key=len, reverse=True):
        if tr and stripped.lower().startswith(tr.lower()):
            return stripped[len(tr):]
    return t


def split_tokens(text: str):
    """按分隔符切成 token 列表（过滤空串）。"""
    return [t for t in _SPLIT_RE.split(text or "") if t]


def _match_enum(tok: str, table: dict):
    """枚举关键词匹配（大小写不敏感，容忍全角/半角）。"""
    if tok in table:
        return table[tok]
    low = tok.lower()
    if low in table:
        return table[low]
    return None


def _match_num(tok: str):
    """数字类参数：返回 (字段名, 值) 或 None。"""
    for prefix, field in _NUM_PREFIXES:
        if tok.startswith(prefix) and len(tok) > len(prefix):
            value = tok[len(prefix):].strip()
            if _NUM_OK.match(value):
                return field, value
            return None
    return None


def _match_text(tok: str):
    """文本类参数（效果图）：返回 (字段名, 值) 或 None。值允许为空。"""
    for prefix, field in _TEXT_PREFIXES:
        if tok.startswith(prefix) and len(tok) > len(prefix):
            value = tok[len(prefix):].lstrip("：:=").strip()
            return field, value
    return None


def parse_wb(text: str, triggers):
    """解析「单卡图」参数。

    返回 (params, unknown)：
      params  —— 仅含被识别到的键，缺省项由调用方补默认值；
      unknown —— 被丢弃的片段（便于调试与回显）。
    """
    body = strip_triggers(text, triggers)
    params = {}
    unknown = []

    for tok in split_tokens(body):
        # 1) 数字类（最长前缀优先）
        hit = _match_num(tok)
        if hit:
            params[hit[0]] = hit[1]
            continue

        # 2) 卡名（前缀粘连）
        name_hit = None
        for prefix in _NAME_PREFIXES:
            if tok.startswith(prefix) and len(tok) > len(prefix):
                name_hit = (prefix, tok[len(prefix):].lstrip("：:=").strip())
                break
        if name_hit:
            params["name"] = name_hit[1]
            continue

        # 3) 特殊框（要在稀有度/种类之前判，避免「普通」被误当职业）
        #    先试枚举表
        sp = _match_enum(tok, CS.SPECIALS)
        if sp is not None:
            params["special"] = sp
            continue

        # 4) 职业
        cls = _match_enum(tok, CS.CLASSES)
        if cls is not None:
            params["class"] = cls
            continue

        # 5) 种类
        kind = _match_enum(tok, CS.KINDS)
        if kind is not None:
            params["kind"] = kind
            continue

        # 6) 稀有度
        rar = _match_enum(tok, CS.RARITIES)
        if rar is not None:
            params["rarity"] = rar
            continue

        # 7) 认不出来的直接丢弃
        unknown.append(tok)

    return params, unknown


def merge_wb_defaults(params: dict) -> dict:
    """把解析结果合并到「单卡图」默认值上，并处理非随从的攻/体失效。"""
    out = dict(CS.DEFAULT_WB)
    out.update(params)
    if out.get("kind", 1) != 1:      # 非随从 → 攻击/体力不生效
        out["atk"] = ""
        out["life"] = ""
    return out


def parse_diy(text: str, triggers):
    """解析「效果图」参数（长文本字段对「关键词 + 内容」形式）。

    正文/进化/超进化 这类字段可能含换行与标点，不能被 `split_tokens` 切碎，
    因此单独用「关键词定位 → 取到下一个关键词之前」的方式抽取。
    """
    body = strip_triggers(text, triggers)
    params = {}
    unknown = []

    # 先抽出长文本字段（按出现位置切片），再从剩余文本里解析常规枚举/数值
    long_fields = [
        ("超进化", "super_evolve"),
        ("进化", "evolve"),
        ("正文", "detail1"),
        ("脚注", "diy"),
        ("画师", "illustrator"),
    ]
    spans = []
    for kw, field in long_fields:
        m = re.search(kw + r"\s*[：:=]?", body)
        if m:
            spans.append((m.start(), m.end(), field))

    spans.sort()
    consumed = []
    for i, (s, e, field) in enumerate(spans):
        end = spans[i + 1][0] if i + 1 < len(spans) else len(body)
        raw = body[e:end]
        # 去掉尾部用于分隔的标点（\u3001，「，」等），保留正文内部的换行与 [hr]
        value = raw.rstrip().rstrip("，,、;；")
        params[field] = value.strip("\n")
        consumed.append((s, end))

    remainder = body
    for s, e in reversed(consumed):
        remainder = remainder[:s] + " " + remainder[e:]

    # 剩余部分按常规 token 解析（职业/种类/稀有度/数值/卡名）
    rest_params, unknown = parse_wb(remainder, triggers=[])
    rest_params.pop("special", None)
    params.update(rest_params)

    return params, unknown


def merge_diy_defaults(params: dict) -> dict:
    """把解析结果合并到「效果图」默认值上。"""
    out = {
        "name": "",
        "class": 0,
        "special": "",
        "kind": 1,
        "rarity": 4,
        "cost": "",
        "atk": "",
        "life": "",
        "detail1": CS.DEFAULT_DIY_TEXT,
        "evolve": "",
        "super_evolve": "",
        "illustrator": "",
        "diy": "",
        "show_evolve": False,
        "show_super": False,
        "show_illustrator": False,
        "show_diy": True,      # 脚注默认 on
    }
    out.update(params)
    # 长文本非空即开启对应区块
    if out.get("evolve"):
        out["show_evolve"] = True
    if out.get("super_evolve"):
        out["show_super"] = True
    if out.get("illustrator"):
        out["show_illustrator"] = True
    if out.get("diy"):
        out["show_diy"] = True
    if out.get("kind", 1) != 1:
        out["atk"] = ""
        out["life"] = ""
    return out
