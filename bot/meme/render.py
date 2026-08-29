# -*- coding: utf-8 -*-
"""把 meme 关键词文本渲染成官方风格列表图（meme_generator 内置 render_meme_list）。

由 D:\\java\\Python 运行（那里装有 meme_generator 0.1.14）。

用法：
    python render.py --in <文本文件(UTF-8,每行一个主关键词)> --out <输出前缀>

结果以一行 JSON 输出到 stdout：
    {"files": ["/abs/前缀.png", ...]}
"""
import argparse
import io
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Literal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PAGE_MAX_H = 5200  # 单张图最大高度，超过则按整行切分成多张


@dataclass
class LegacyMemeProperties:
    disabled: bool = False
    labels: list[Literal["new", "hot"]] = field(default_factory=list)


def _load_meme_map():
    from meme_generator import get_memes

    mapping = {}
    for meme in get_memes():
        mapping[meme.key] = meme
        for kw in getattr(meme, "keywords", None) or []:
            if kw:
                mapping[kw] = meme
    return mapping


def _split(img):
    """超长图按完整行（块高 50px）纵向切分，避免文字被拦腰截断。"""
    width, height = img.size
    if height <= PAGE_MAX_H:
        return [img]
    block_h = 50
    pages = []
    y = 0
    while y < height:
        bottom = min(y + PAGE_MAX_H, height)
        bottom = (bottom // block_h) * block_h
        if bottom <= y:
            bottom = height
        pages.append(img.crop((0, y, width, bottom)))
        if bottom == height:
            break
        y = bottom
    return pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="out", required=True)
    args = ap.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    from PIL import Image
    from meme_generator.utils import render_meme_list

    meme_map = _load_meme_map()
    seen = set()
    meme_list = []
    for kw in lines:
        meme = meme_map.get(kw)
        if meme is None or meme.key in seen:
            continue
        seen.add(meme.key)
        meme_list.append((meme, LegacyMemeProperties(labels=[])))

    rendered = render_meme_list(
        meme_list=meme_list,
        text_template="{index}.{keywords}",
        add_category_icon=True,
    )
    img = Image.open(rendered).convert("RGB")

    files = []
    pages = _split(img)
    for i, page in enumerate(pages, 1):
        out = f"{args.out}.png" if len(pages) == 1 else f"{args.out}_p{i}.png"
        page.save(out, "PNG")
        files.append(os.path.abspath(out))
    print(json.dumps({"files": files}, ensure_ascii=False))


if __name__ == "__main__":
    main()
