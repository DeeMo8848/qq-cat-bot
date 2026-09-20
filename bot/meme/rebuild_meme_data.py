# -*- coding: utf-8 -*-
"""以「当前实际加载的模板」为唯一事实来源，重建 meme_data.py。

与已有两个脚本的区别：
- rebuild_data.py         : 枚举全部模板的所有关键词（含歧义词会被合并丢一个）
- build_data_whitelist.py : 以旧白名单为准（白名单里失效的词会留下幽灵条目）
- 本脚本 (rebuild_meme_data.py) : 以实际模板为准 + 可选白名单过滤 + 死映射清零

核心保证：
  1. KW 里的每个 value 都必须是「实际能加载的模板 key」
  2. META 只包含 KW 引用到的 key
  3. 可选：若提供白名单文件，只保留白名单里的关键词（用于控制触发词数量）

用法：
    python bot/meme/rebuild_meme_data.py            # 全量（所有模板的关键词）
    python bot/meme/rebuild_meme_data.py --whitelist cache/meme_list_kw.txt
"""
import argparse
import json
import os
import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))


def load_all_memes():
    """加载内置模板 + 项目自定义模板，返回 manager（已注册的模板）。"""
    import meme_generator
    from meme_generator.config import meme_config
    from meme_generator import manager

    builtin = Path(meme_generator.__file__).resolve().parent / "memes"
    meme_config.meme.load_builtin_memes = True

    manager._memes.clear()
    manager.load_memes(str(builtin))

    # 项目自带自定义模板：逐个子目录加载（比整目录加载更可靠）
    custom_root = _PROJ_ROOT / "bot" / "meme" / "custom_memes"
    if custom_root.is_dir():
        for sub in sorted(custom_root.iterdir()):
            if sub.is_dir() and (sub / "__init__.py").is_file():
                try:
                    manager.load_memes(str(sub))
                except Exception as e:
                    print("  [warn] 加载失败 %s: %s" % (sub.name, e), file=sys.stderr)

    # meme_config 里额外配置的目录（例如本机开发时的 meme-demo）
    for d in (meme_config.meme.meme_dirs or []):
        d = str(d)
        if os.path.isdir(d):
            try:
                manager.load_memes(d)
            except Exception:
                pass

    return manager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whitelist", default=None,
                    help="可选：关键词白名单文件（每行一个词），只保留其中的关键词")
    ap.add_argument("--out", default=None, help="输出路径，默认 bot/meme/meme_data.py")
    args = ap.parse_args()

    manager = load_all_memes()

    # 收集：key -> 元数据；kw -> 候选 key 集合
    KEY_META = {}
    kw2keys = {}
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
            kw2keys.setdefault(kw, set()).add(key)

    # 唯一映射（避免歧义触发）
    uniq = {kw: next(iter(ks)) for kw, ks in kw2keys.items() if len(ks) == 1}

    # 可选白名单过滤
    if args.whitelist:
        wl = Path(args.whitelist)
        if wl.is_file():
            words = [l.strip() for l in open(wl, encoding="utf-8") if l.strip()]
            before = len(uniq)
            uniq = {w: uniq[w] for w in words if w in uniq}
            print("白名单过滤: %d -> %d（白名单 %d 词）" % (before, len(uniq), len(words)))
        else:
            print("白名单不存在，忽略:", wl, file=sys.stderr)

    # 死映射自检：KW 的 value 必须都在 KEY_META 中
    used = set(uniq.values())
    missing = [k for k in used if k not in KEY_META]
    if missing:
        print("!! 有 %d 个 key 不在已加载模板中，予以剔除: %s" % (len(missing), missing[:10]), file=sys.stderr)
        uniq = {kw: k for kw, k in uniq.items() if k in KEY_META}
        used = set(uniq.values())

    out_meta = {k: KEY_META[k] for k in sorted(used)}

    out_path = Path(args.out) if args.out else (_PROJ_ROOT / "bot" / "meme" / "meme_data.py")
    head = (
        "# -*- coding: utf-8 -*-\n"
        '"""meme 关键词静态数据（由 rebuild_meme_data.py 生成，勿手改；可用「meme更新」重建）。\n\n'
        "KW: 关键词 -> 唯一对应的 meme 模板 key\n"
        "META: key -> {min_images,max_images,min_texts,max_texts,default_texts,args_options}\n"
        "仅收录能唯一映射到单个 key 的关键词，且保证每个 key 都真实存在于已加载模板中。\n"
        '"""\n'
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head)
        f.write("KW = %s\n" % json.dumps(uniq, ensure_ascii=False))
        f.write("META = %s\n" % json.dumps(out_meta, ensure_ascii=False))

    print("完成: KW=%d META=%d -> %s" % (len(uniq), len(out_meta), out_path))
    print("死映射: 0（已自检）")

    # 抽查几个关注的关键词
    for kw in ("随机狂粉", "更多狂粉", "肥鱼说", "肥鱼举牌"):
        print("  %s -> %s" % (kw, uniq.get(kw, "(无)")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
