# -*- coding: utf-8 -*-
"""肥鱼举牌：让角色在气泡里说出你想说的话。

触发词：肥鱼说 / 肥鱼举牌
用法：  肥鱼说 要显示的文本（支持 \\n 换行；【】内的文字渲染成紫色）
示例：  肥鱼说 吾辈现在不想说话
        肥鱼说 第一行\\n第二行

移植自 astrbot_plugin_handsign_memes；原插件依赖 sketchbook 库（本地未安装），
此处用 pil_utils / PIL 重写了「文字自适应缩放 + 换行 + 【】紫色」逻辑，效果保持一致。
"""

import io
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pil_utils import BuildImage
from meme_generator import add_meme

plugin_dir = Path(__file__).parent
BASE_IMAGE = plugin_dir / "images" / "base.png"
FONT_PATH = plugin_dir / "fonts" / "wqy-microhei.ttc"

# 文字绘制区域 (x1, y1, x2, y2)，坐标原点在左上角；取自原插件 constants.TEXT_REGION
TEXT_REGION = (150, 150, 620, 470)

TEXT_COLOR = (45, 38, 35, 255)        # 普通文字（深色）
BRACKET_COLOR = (156, 88, 207, 255)   # 【】内文字（紫色）
MAX_FONT_HEIGHT = 200                 # 字号上限；短文字放大撑满，长文字自动缩小


def _parse_segments(text: str):
    """把文本拆成 [(片段, 颜色), ...]，【】内的片段用紫色。"""
    parts = re.split(r"(【[^】]*】)", text)
    segs = []
    for p in parts:
        if not p:
            continue
        if p.startswith("【") and p.endswith("】"):
            segs.append((p[1:-1], BRACKET_COLOR))
        else:
            segs.append((p, TEXT_COLOR))
    return segs


def _layout(segs, font):
    """计算文本在区域内最合适的字号并排版。

    返回 (lines, font)。lines: 每行是一个 [(seg, color)] 列表。
    采用二分搜索，在 [10, MAX_FONT_HEIGHT] 内取「能完整放进气泡」的最大字号。
    行高用字体真实度量（ascent + descent）计算，确保中文不溢出气泡；
    换行宽度按逐段累加测量，与最终绘制保持一致。
    """
    x1, y1, x2, y2 = TEXT_REGION
    box_w = x2 - x1
    box_h = y2 - y1

    draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    def line_width(line, f):
        return sum(draw.textlength(seg, font=f) for seg, _ in line)

    def flow(f):
        lines, cur = [], []
        for seg, col in segs:
            # 单个片段也按字拆开换行（避免长词溢出）；显式 \n 强制换行
            for ch in seg:
                if ch == "\n":
                    if cur:
                        lines.append(cur)
                        cur = []
                    continue
                if cur and line_width(cur + [(ch, col)], f) > box_w:
                    lines.append(cur)
                    cur = []
                cur.append((ch, col))
        if cur:
            lines.append(cur)
        return lines

    def height_ok(f, lines):
        ascent, descent = f.getmetrics()  # 真实字体度量，中文字符全高
        line_h = (ascent + descent) * 1.25
        return len(lines) * line_h <= box_h

    low, high = 10, MAX_FONT_HEIGHT
    best_f = ImageFont.truetype(str(FONT_PATH), 10)
    best_lines = flow(best_f)
    while low <= high:
        mid = (low + high) // 2
        f = ImageFont.truetype(str(FONT_PATH), mid)
        lines = flow(f)
        if height_ok(f, lines):
            best_f, best_lines = f, lines
            low = mid + 1
        else:
            high = mid - 1
    return best_lines, best_f


def _draw_handsign(text: str) -> io.BytesIO:
    x1, y1, x2, y2 = TEXT_REGION
    segs = _parse_segments(text)
    lines, font = _layout(segs, None)

    base = Image.open(BASE_IMAGE).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    box_w = x2 - x1
    box_h = y2 - y1
    # 与 _layout 用同一套度量，保证垂直居中后整段不溢出气泡
    ascent, descent = font.getmetrics()
    line_h = (ascent + descent) * 1.25
    total_h = len(lines) * line_h
    cur_y = y1 + (box_h - total_h) / 2

    for line in lines:
        total_w = sum(draw.textlength(seg, font=font) for seg, _ in line)
        cur_x = x1 + (box_w - total_w) / 2
        # 逐段绘制保留【】内文字的紫色高亮
        for seg, color in line:
            draw.text((cur_x, cur_y), seg, font=font, fill=color)
            cur_x += draw.textlength(seg, font=font)
        cur_y += line_h

    base.alpha_composite(layer)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf


def feiyu(images, texts: list[str], args):
    text = texts[0] if texts else "你在说什么呢"
    text = str(text).strip().replace("\\n", "\n")
    return _draw_handsign(text)


add_meme(
    "feiyu",
    feiyu,
    min_texts=0,
    max_texts=1,
    default_texts=["你在说什么呢"],
    keywords=["肥鱼说", "肥鱼举牌"],
    date_created=datetime(2026, 8, 29),
    date_modified=datetime(2026, 8, 29),
)