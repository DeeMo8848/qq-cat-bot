# -*- coding: utf-8 -*-
"""影之诗制卡器 bot 插件 —— 参数表与默认值。

**本文件是「原始工具参数的唯一真相映射表」**，取值全部对齐官方开源版前端
`web/app.js` / `web/index.html` / `src/card.rs`：

| 项 | 原始工具 | 本插件 |
|---|---|---|
| 职业 | `<select name="class">` 0..7 | CLASSES |
| 特殊框 | `<select name="special">` ""/style_101 | SPECIALS |
| 种类 | `<select name="kind">` 1/2/3 | KINDS |
| 稀有度 | `<select name="rarity">` 1..4 | RARITIES |
| frame | `` `${kindName}_${special || rarityName}` `` | build_frame() |
| 尺寸 | DIY_SIZES × 0.4（81 → 32.4） | DIY_SIZES |

修改任何映射前，请先回原项目核对上表出处。
"""

# ---- 职业：0=中立 .. 7=超越者 ----
# 对齐 app.js `CLASS_LABELS.chs`
CLASS_LABELS_CHS = ["中立", "精灵", "皇家护卫", "巫师", "龙族", "梦魇", "主教", "超越者"]

CLASSES = {
    "中立": 0,
    "精灵": 1, "妖精": 1,
    "皇家": 2, "皇家护卫": 2, "皇": 2,
    "巫师": 3, "法师": 3,
    "龙族": 4, "龙": 4,
    "梦魇": 5, "暗影": 5,
    "主教": 6,
    "超越者": 7, "复仇者": 7,
    # 英文（便于混输）
    "neutral": 0, "forestcraft": 1, "swordcraft": 2, "runecraft": 3,
    "dragoncraft": 4, "abysscraft": 5, "havencraft": 6, "portalcraft": 7,
}

# ---- 种类 ----
KINDS = {
    "随从": 1, "从者": 1, "follower": 1,
    "护符": 2, "amulet": 2, "amullet": 2,
    "法术": 3, "spell": 3, "魔法": 3,
}

# ---- 稀有度 ----
RARITIES = {
    "铜": 1, "青铜": 1, "bronze": 1,
    "银": 2, "白银": 2, "silver": 2,
    "金": 3, "黄金": 3, "gold": 3,
    "虹": 4, "传说": 4, "legend": 4, "传奇": 4,
}

# ---- 特殊框（""=普通）----
SPECIALS = {
    "普通": "",
    "特殊": "style_101",
    "style_101": "style_101",
    "特殊框": "style_101",
}

# ---- frame 推导（对齐 app.js collectConfig）----
KIND_KEYS = {1: "follower", 2: "amulet", 3: "spell"}
RARITY_KEYS = {1: "bronze", 2: "silver", 3: "gold", 4: "legend"}

# ---- DIY（效果图）尺寸：UI 值 81 × 0.4 = 32.4 ----
DIY_SIZES = {"d1": 81, "d2": 81, "ev": 81, "super": 81, "cre": 81}
DIY_SIZE_FACTOR = 0.4
DIY_BG_TYPE = 2          # 一代暗色背景功能已移除，固定 2
DIY_BG_ALPHA = 0.3       # UI 默认 range 值 30 → 0.3

# ---- 默认值：单卡图 ----
DEFAULT_WB = {
    "name": "",
    "class": 0,          # 中立
    "special": "",       # 普通
    "kind": 1,           # 随从
    "rarity": 4,         # 虹
    "cost": "",          # 空白（注意：原工具 UI 默认是 "1"，此处按 bot 需求留空）
    "atk": "",
    "life": "",
}

# ---- 默认值：效果图（正文那段默认文案来自需求方指定）----
DEFAULT_DIY_TEXT = (
    "【入场曲】对对手的战场上的所有随从造成5点伤害。回复自己的主战者5点生命值。\n"
    "[hr]\n"
    "【疾驰】\n"
    "【守护】\n"
    "【灵气】"
)


def build_frame(kind: int, rarity: int, special: str) -> str:
    """复刻 app.js：frame = `${kindName}_${special || rarityName}`。"""
    kind_name = KIND_KEYS.get(kind, "follower")
    if special:
        return "%s_%s" % (kind_name, special)
    return "%s_%s" % (kind_name, RARITY_KEYS.get(rarity, "legend"))


def class_label(cls: int) -> str:
    """职业的中文标签（效果图标题带用）。"""
    if 0 <= cls < len(CLASS_LABELS_CHS):
        return CLASS_LABELS_CHS[cls]
    return CLASS_LABELS_CHS[0]
