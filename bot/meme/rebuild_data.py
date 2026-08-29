# -*- coding: utf-8 -*-
"""重建 meme 关键词静态数据 bot/meme_data.py（由 D:\\java\\Python 运行）。

在「meme更新」时调用：重新枚举当前所有 meme（内置 + meme-demo 扩展），
把唯一映射的关键词与模板元数据写回 meme_data.py。
"""

import json
import os
import sys
from pathlib import Path

# 本项目根目录
_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
from config import MEME_CUSTOM_DIR

BUILTIN = None  # 运行时根据 meme_generator 安装位置定位
DEMO = MEME_CUSTOM_DIR
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meme_data.py")


def main():
    import meme_generator
    from meme_generator.config import meme_config

    # 自动定位 pip 安装的 meme_generator 自带 meme 目录，避免写死具体 Python 路径
    global BUILTIN
    BUILTIN = str(Path(meme_generator.__file__).resolve().parent / "memes")

    meme_config.meme.load_builtin_memes = True
    meme_dirs = list(meme_config.meme.meme_dirs or [])
    if DEMO not in meme_dirs:
        meme_dirs.append(DEMO)

    from meme_generator import manager

    manager._memes.clear()
    manager.load_memes(BUILTIN)
    for d in meme_dirs:
        try:
            manager.load_memes(d)
        except Exception:
            pass

    KEYWORDS = {}
    KEY_META = {}
    for m in manager.get_memes():
        key = m.key
        if key in KEY_META:
            continue
        p = m.params_type
        args_options = []
        if p.args_type:
            for opt in p.args_type.parser_options:
                desc = "|".join(opt.names)
                if opt.args:
                    desc += " " + ", ".join(a.name for a in opt.args)
                args_options.append(desc)
        KEY_META[key] = {
            "min_images": p.min_images, "max_images": p.max_images,
            "min_texts": p.min_texts, "max_texts": p.max_texts,
            "default_texts": list(p.default_texts),
            "args_options": args_options,
        }
        seen = set()
        for kw in (m.keywords or []):
            kw = (kw or "").strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            KEYWORDS.setdefault(kw, set()).add(key)

    KW = {kw: next(iter(keys)) for kw, keys in KEYWORDS.items() if len(keys) == 1}

    head = (
        "# -*- coding: utf-8 -*-\n"
        '"""meme 关键词静态数据（由 rebuild_data.py 生成，勿手改；可用「meme更新」重建）。\n\n'
        "KW: 关键词 -> 唯一对应的 meme 模板 key\n"
        "META: key -> {min_images,max_images,min_texts,max_texts,default_texts,args_options}\n"
        '仅收录能唯一映射到单个 key 的关键词，避免歧义触发。\n'
        '"""\n'
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(head)
        f.write("KW = %s\n" % json.dumps(KW, ensure_ascii=False))
        f.write("META = %s\n" % json.dumps(KEY_META, ensure_ascii=False))
    print("OK KW=%d META=%d" % (len(KW), len(KEY_META)))


if __name__ == "__main__":
    main()