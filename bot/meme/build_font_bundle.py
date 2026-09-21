# -*- coding: utf-8 -*-
"""构建内置字体包 `bot/assets/fonts/qqbot-fonts.ttc`。

### 背景

meme-generator 的模板按**字体族名**指定字体（`font_families=["Noto Sans SC"]`），
但它自身不带字体，指望宿主系统装好。干净 Linux 服务器上没有任何中文字体，
导致 127 个模板渲染出「口口口口」。详见 `bot/core/fonts.py` 的模块文档。

### 本脚本做什么

从一款中文字体出发，用 `fontTools` 改写其 `name` 表，造出若干「族名别名」，
再打包进 **TTC（TrueType Collection）**。

选用 TTC 而不是多份副本，是因为 TTC 内多个字体条目**共享 `glyf`/`loca`/`cmap`
等大表**，只有小的 `name` 表各不相同。实测 21 个别名合计 3.6MB，
而复制 21 份需要 74MB。

### 用法

    python bot/meme/build_font_bundle.py
    python bot/meme/build_font_bundle.py --src <字体路径> --out <输出.ttc>

依赖 `fontTools`（meme 侧解释器已装）。运行后会打印每个族名的
`unicharToGlyph('我')` 结果与最终体积，便于校验。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 本项目根目录
_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))

# 默认源字体：项目内已有的中文黑体（3.5MB，10413 字符，1172 常用汉字缺 0）
DEFAULT_SRC = _PROJ_ROOT / "bot" / "parse" / "resources" / "HYSongYunLangHeiW-1.ttf"

# 默认输出
DEFAULT_OUT = _PROJ_ROOT / "bot" / "assets" / "fonts" / "qqbot-fonts.ttc"

# 备选源字体（若默认不存在则尝试；wqy 字形更全）
FALLBACK_SRCS = (
    _PROJ_ROOT / "bot" / "meme" / "custom_memes" / "feiyu" / "fonts" / "wqy-microhei.ttc",
)


def _pick_src(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit("指定的源字体不存在：%s" % p)
        return p
    if DEFAULT_SRC.is_file():
        return DEFAULT_SRC
    for p in FALLBACK_SRCS:
        if p.is_file():
            print("[warn] 默认源字体缺失，改用 %s" % p, file=sys.stderr)
            return p
    raise SystemExit("找不到任何可用的源字体，请用 --src 指定")


def _alias_name_table(ft, family: str) -> None:
    """把字体 name 表的族名相关记录改写成 `family`。

    需要同时改 records 与保存在 `name` 表的统一结构，fontTools 会同步。
    nameID 含义：1=Family 2=Subfamily 3=UniqueID 4=FullName 6=PostScript
                 16=Typographic Family 17=Typographic Subfamily
    """
    ps_name = family.replace(" ", "")
    for rec in ft["name"].names:
        if rec.nameID in (1, 16):
            rec.string = family
        elif rec.nameID == 4:
            rec.string = family
        elif rec.nameID == 6:
            rec.string = ps_name
        elif rec.nameID == 3:
            rec.string = "%s;qqbot-font-alias" % family
    # fontTools 对部分字体缓存了 name 查询结果，显式失效
    try:
        ft["name"].names = ft["name"].names
    except Exception:
        pass


def build(src: Path, out: Path, aliases: tuple[str, ...]) -> int:
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.ttCollection import TTCollection

    # wqy-microhei.ttc 是字体集合，取第 0 号（Regular）
    kw = {"fontNumber": 0} if src.suffix.lower() in (".ttc", ".otc") else {}
    print("源字体：%s (%.2f MB)" % (src, src.stat().st_size / 1048576))

    fonts = []
    for fam in aliases:
        ft = TTFont(str(src), **kw)
        _alias_name_table(ft, fam)
        fonts.append(ft)
    print("已生成 %d 个族名条目" % len(fonts))

    out.parent.mkdir(parents=True, exist_ok=True)
    coll = TTCollection()
    coll.fonts = fonts
    coll.save(str(out))

    size = out.stat().st_size
    naive = len(fonts) * src.stat().st_size
    print("写出：%s" % out)
    print("体积：%.2f MB（若用副本需 %.2f MB，省 %.0f%%）"
          % (size / 1048576, naive / 1048576,
             100.0 * (1 - size / max(naive, 1))))

    # ---- 校验：Skia 能否识别全部族名，且中文有真实字形 ----
    try:
        import skia
        mgr = skia.FontMgr.New_Custom_Directory(str(out.parent))
        got = {mgr.getFamilyName(i) for i in range(mgr.countFamilies())}
        print()
        print("Skia 识别到 %d 个族名：" % len(got))
        ok = True
        for fam in aliases:
            tf = mgr.matchFamilyStyle(fam, skia.FontStyle())
            if tf is None:
                print("   [缺失] %s" % fam)
                ok = False
                continue
            gid = tf.unicharToGlyph(ord("我"))
            mark = "OK " if gid else "!! "
            if not gid:
                ok = False
            print("   [%s] %-24s glyphId(我)=%s" % (mark, fam, gid))
        print()
        print("结论：%s" % ("全部族名可用，中文均有真实字形" if ok else "存在缺失项，请检查"))
        return 0 if ok else 1
    except ImportError:
        print()
        print("（未安装 skia，跳过校验）")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建内置字体包")
    ap.add_argument("--src", help="源字体路径（默认项目内 HYSongYunLangHeiW-1.ttf）")
    ap.add_argument("--out", help="输出 .ttc 路径")
    args = ap.parse_args()

    src = _pick_src(args.src)
    out = Path(args.out) if args.out else DEFAULT_OUT

    try:
        from bot.core.fonts import ALIAS_FAMILIES
    except Exception as e:
        raise SystemExit("无法导入 bot.core.fonts（需在项目根运行）：%s" % e)

    return build(src, out, ALIAS_FAMILIES)


if __name__ == "__main__":
    sys.exit(main())
