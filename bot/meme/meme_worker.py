# -*- coding: utf-8 -*-
"""meme 子进程生成器（由 D:\\java\\Python 运行，meme-generator 装在那边）。

被 bot 通过 run_script 调用，与机器人运行环境(TRAE Python)隔离，互不污染。

用法：
    python meme_worker.py --key <key> --out <输出文件路径> \
        [--image <图片路径>]... [--text <文本>]... [--opt k=v]...

结果以一行 JSON 输出到 stdout：
    {"ok": true, "file": "/abs/path/到/生成文件"}
    或 {"ok": false, "error": "错误信息"}
"""

import argparse
import io
import json
import sys
from pathlib import Path

# 本项目根目录：bot/meme/ 往上一级到 bot/ 再往上到项目根
_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent


def _init():
    # 把项目根加入 sys.path，读取 config 里解析好的自定义 meme 目录
    sys.path.insert(0, str(_PROJ_ROOT))
    from config import MEME_CUSTOM_DIR

    # 加载内置 + 用户扩展（项目内 custom_memes），确保全部模板可用
    from meme_generator.config import meme_config

    meme_config.meme.load_builtin_memes = True
    if MEME_CUSTOM_DIR not in meme_config.meme.meme_dirs:
        meme_config.meme.meme_dirs.append(MEME_CUSTOM_DIR)

    from meme_generator import manager
    import meme_generator

    # 自动定位 pip 安装的 meme_generator 自带 meme 目录，避免写死具体 Python 路径
    builtin = Path(meme_generator.__file__).resolve().parent / "memes"
    manager._memes.clear()
    try:
        manager.load_memes(str(builtin))
    except Exception:
        pass
    for d in meme_config.meme.meme_dirs:
        try:
            manager.load_memes(d)
        except Exception:
            pass


def _detect_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"WEBP":
        return ".webp"
    return ".gif"


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--text", action="append", default=[])
    ap.add_argument("--opt", action="append", default=[])
    args = ap.parse_args()

    _init()
    try:
        from meme_generator import get_meme

        meme = get_meme(args.key)
        opts = {}
        for o in args.opt:
            if "=" in o:
                k, v = o.split("=", 1)
                opts[k] = v
            else:
                opts[o] = True
        result = meme(images=args.image, texts=args.text, args=opts)
        data = result.getvalue()
        ext = _detect_ext(data)
        outfile = args.out + ext if not args.out.lower().endswith(
            (".gif", ".png", ".jpg", ".jpeg", ".webp")
        ) else args.out
        with open(outfile, "wb") as f:
            f.write(data)
        print(json.dumps({"ok": True, "file": outfile}, ensure_ascii=False))
    except Exception as e:
        # 拆解 meme 框架自带的参数不足错误，方便 bot 体面返回提示
        msg = str(e)
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))


if __name__ == "__main__":
    main()