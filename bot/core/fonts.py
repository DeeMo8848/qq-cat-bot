# -*- coding: utf-8 -*-
"""内置字体：让所有图像渲染路径不再依赖宿主机装了什么字体。

### 为什么需要它

meme-generator 的每个模板都用 **字体族名**（family name）指定字体，例如：

    5000choyen : font_families = ["Noto Sans SC", "Noto Serif SC"]
    fanatic    : （不指定，走 pil_utils 的 DEFAULT_FALLBACK_FONTS 兜底）
    ... 共 127 个模板引用了 FZShaoEr-M11S / FZXS14 / Noto Sans SC 等族名

而 `meme_generator` 自己 **一个字体文件都不带**，它假定宿主系统装好了这些字体。
偏偏干净的 Linux 服务器上连一个中文字体都没有（实测 Skia 只能看到 `Noto Sans`
一个拉丁族），于是 Skia 匹配不到族名 → 落到没有 CJK 字形的兜底字体 →
渲染出来全是 **「口口口口」**。

`pil_utils` 里只有一条文字渲染路径（`BuildImage.draw_text` 等方法最终都调用
`Text2Image.from_text`），它们共用模块级单例 `pil_utils.text2image.font_collection`。
所以只要在渲染开始前把该单例的 FontManager 换成「包含项目内置字体」的版本，
**所有 meme 一次性全部修好**，无需改动第三方库、也无需逐个模板打补丁。

### 实现要点

1. `skia.FontMgr.New_Custom_Directory(dir)` 是**独占**的 —— 一旦用它，
   系统字体全部不可见（实测 `Noto Sans SC` 从可解析变成 None）。
   因此必须把**所有**需要的字体集中放进同一个目录再注册。

2. Skia 按族名匹配，而内置字体自己的族名（如 `HYSongYunLangHeiW`）并不在
   meme 引用的族名列表里。解决办法是用 `fontTools` 改写字体 `name` 表，
   造出「族名别名」。**难点是体积**：直接复制 N 份 3.5MB 字体 = N×3.5MB。
   改用 **TTC（字体集合）**：多个族名共享同一份 `glyf`/`cmap` 表，
   实测 6 个别名合计仅 3.56MB（对比 21.3MB）。

3. `bot/assets/fonts/` 是**需要入库的项目资产**（不能在 .gitignore 里），
   这样换设备/换服务器都自带字体，不需要在每台机器上装字体。

4. **emoji 单独一个文件**（`NotoColorEmoji.ttf`，约 10MB，Noto Color Emoji，
   OFL-1.1 协议可自由分发）。它是 CBDT/CBLC 彩色位图格式，塞进 TTC 会让字体包
   从 3.5MB 膨胀到 14MB；更重要的是 emoji 与文字字体是两类资源，
   分开存放便于各自独立更新。`install()` 扫描整个目录，所以无需额外代码。

### 与其他渲染路径的关系

- `bot/parse/render.py`、`bot/parse/parsers/mcmod.py` 早已用自己的内置字体
  （`HYSongYunLangHeiW-1.ttf`），不依赖本模块。
- `plugins/jrys/jrys.py` 走 `bot.core.platform.load_cjk_font(project_root=ROOT)`，
  也已经是内置优先。
- **已自定义字体的 meme（如 feiyu 符箓用 `ImageFont.truetype(绝对路径)`）
  不受影响** —— 它们不过族名这条路径，本模块只替换默认 FontManager，
  不会、也不该覆盖这类显式指定。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 内置字体资产目录（相对项目根）
FONT_DIR_PARTS = ("bot", "assets", "fonts")

# 字体集合文件名（由 tools/build_font_bundle.py 生成）
BUNDLE_NAME = "qqbot-fonts.ttc"

# 需要覆盖的族名别名 —— 来自 meme_generator 全部模板的 font_families 统计。
# 数量降序：FZShaoEr-M11S(72) / FZXS14(27) / FZSJ-QINGCRJ(16) / FZKaTong-M19S(13)
#          / Noto Serif SC(2) / Consolas(2) / Noto Sans SC(1) 等
# 另附 pil_utils.DEFAULT_FALLBACK_FONTS 里的中文项，兜住不指定字体的模板。
ALIAS_FAMILIES = (
    # —— meme_generator 模板显式引用的族名 ——
    "FZShaoEr-M11S",
    "FZXS14",
    "FZSJ-QINGCRJ",
    "FZKaTong-M19S",
    "FZPangWa-M18S",
    "Noto Sans SC",
    "Noto Serif SC",
    "Source Han Sans SC",
    "Noto Sans CJK SC",
    "Glow Sans SC",
    "PangMenZhengDao-Cu",
    "033-SSFangTangTi",
    # —— pil_utils.DEFAULT_FALLBACK_FONTS 中的中文 / 通用项 ——
    "Microsoft YaHei",
    "WenQuanYi Micro Hei",
    "PingFang SC",
    "Hiragino Sans GB",
    # —— 等宽（Consolas 在部分模板里用于英文/代码风格文字）——
    "Consolas",
    # —— 拉丁兜底（避免英文也落到豆腐块）——
    "Arial",
    "Tahoma",
    "Segoe UI",
    "Helvetica Neue",
)

# emoji 字体族名。
# ★ 这些族名**不由 TTC 提供** —— 彩色 emoji 字体是 CBDT/CBLC 位图格式（约 10MB），
#   打进 TTC 会让字体包从 3.5MB 涨到 14MB，且它是「独立于文字字体」的一类资源，
#   所以单独放 `NotoColorEmoji.ttf`，由 install() 扫描目录时一并注册。
#
# ★ 为什么需要单独一张表：emoji 字形**不在**中文 TTC 里（实测 18/18 全缺），
#   而 Skia 按族名查字体 —— 不注册这些族名，`😀` 一样会渲染成豆腐块。
#   注：emoji 一般走 Skia 的自动字符回退，正常文字渲染不依赖显式指定族名，
#   这里登记是为了让「显式写 font_families=["Noto Color Emoji"]」的模板也能命中。
EMOJI_FAMILIES = (
    "Noto Color Emoji",
    "Apple Color Emoji",
    "Segoe UI Emoji",
    "Segoe UI Symbol",
    "EmojiOne Color",
    "Twemoji Mozilla",
)

_installed_paths: list[str] = []


def font_dir(project_root: str | os.PathLike | None = None) -> Path:
    """返回内置字体目录的绝对路径。"""
    if project_root is None:
        # 本文件在 bot/core/ 下，往上两级到项目根
        project_root = Path(__file__).resolve().parent.parent.parent
    return Path(project_root).joinpath(*FONT_DIR_PARTS)


def install(project_root: str | os.PathLike | None = None,
            verbose: bool = False) -> list[str]:
    """把内置字体注入 pil_utils 的全局 font_collection。

    返回实际注册的字体文件列表（空列表表示没找到或注入失败）。
    幂等：重复调用只会重复设置同一个目录，代价可忽略。
    失败一律静默降级 —— 字体问题绝不能拖垮整个渲染流程。
    """
    global _installed_paths

    def log(msg: str):
        if verbose:
            print("[fonts] %s" % msg, file=sys.stderr, flush=True)

    d = font_dir(project_root)
    if not d.is_dir():
        log("字体目录不存在，跳过：%s" % d)
        return []

    # 收集目录内所有字体文件（.ttc 字体集合优先，Skia 能从一个文件读出多个族名）
    files: list[str] = []
    try:
        for name in sorted(os.listdir(d)):
            low = name.lower()
            if low.endswith((".ttc", ".ttf", ".otf", ".otc")):
                fp = os.path.join(d, name)
                if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                    files.append(fp)
    except OSError as e:
        log("读取字体目录失败：%s" % e)
        return []

    if not files:
        log("字体目录为空：%s" % d)
        return []

    try:
        import importlib
        import skia
        # ★ 必须用 importlib.import_module 取真模块：
        # meme_generator 顶层导出了一个同名函数 `text2image`，
        # 直接 `import pil_utils.text2image as m` 会拿到那个函数而不是模块。
        _t2i = importlib.import_module("pil_utils.text2image")
    except Exception as e:  # pragma: no cover - 依赖缺失时静默
        log("skia / pil_utils 不可用：%s" % e)
        return []

    try:
        mgr = skia.FontMgr.New_Custom_Directory(str(d))
        if mgr is None or mgr.countFamilies() <= 0:
            log("目录未解析出任何字体族：%s" % d)
            return []
        _t2i.font_collection.setDefaultFontManager(mgr)
        families = [mgr.getFamilyName(i) for i in range(mgr.countFamilies())]
        log("已注册 %d 个字体族：%s" % (len(families), families))
    except Exception as e:  # pragma: no cover
        log("注入失败：%s: %s" % (type(e).__name__, e))
        return []

    _installed_paths = files
    return files


def installed() -> list[str]:
    """返回上次 install() 实际注册的字体文件（未调用过则为空）。"""
    return list(_installed_paths)


def families(project_root: str | os.PathLike | None = None) -> list[str]:
    """探测当前目录能提供哪些字体族名（调试用）。"""
    d = font_dir(project_root)
    if not d.is_dir():
        return []
    try:
        import skia
        mgr = skia.FontMgr.New_Custom_Directory(str(d))
        return [mgr.getFamilyName(i) for i in range(mgr.countFamilies())]
    except Exception:
        return []
