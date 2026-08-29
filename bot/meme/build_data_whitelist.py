# -*- coding: utf-8 -*-
"""精确重建 meme_data.py：以旧白名单(meme_list_kw.txt 620词)为准，只追加 feiyu。

不引入 meme-demo 里其余自定义模板的关键词，避免污染现有稳定触发表。
生成后通过「meme刷新」热加载即可。
"""

import json
import re

from meme_generator.config import meme_config
from meme_generator import manager

BUILTIN = r"D:\java\Python\Lib\site-packages\meme_generator\memes"
DEMO = r"C:\Users\DeeMo\meme-demo\memes"
OLD_WHITELIST = r"D:\jsq\qqbot\cache\meme_list_kw.txt"
OUT = r"D:\jsq\qqbot\bot\meme\meme_data.py"

# 旧白名单中的歧义词：在新枚举下无法唯一映射，按旧逻辑一并剔除
DROPPED = {"anan_hs", "acacia_anan_holdsign"}

# 本次唯一新增：feiyu 的两个触发词
FEIYU_EXTRA = {"肥鱼说", "肥鱼举牌"}

meme_config.meme.load_builtin_memes = True
d = list(meme_config.meme.meme_dirs or [])
if DEMO not in d:
    d.append(DEMO)
manager._memes.clear()
manager.load_memes(BUILTIN)
for dd in d:
    try:
        manager.load_memes(dd)
    except Exception:
        pass

# 全量唯一映射
mapping = {}
for m in manager.get_memes():
    seen = set()
    for kw in (m.keywords or []):
        kw = (kw or "").strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        mapping.setdefault(kw, set()).add(m.key)
all_kw2key = {kw: next(iter(s)) for kw, s in mapping.items() if len(s) == 1}

# 目标关键词集：旧白名单(去掉歧义) + feiyu 两个词，只保留能唯一映射的
old_lines = [l.strip() for l in open(OLD_WHITELIST, encoding="utf-8") if l.strip()]
target = set(f for f in old_lines if f not in DROPPED) | FEIYU_EXTRA
target = {w for w in target if w in all_kw2key}

# 生成 KW 与 META（META 只含被 KW 引用的 key）
KW = {w: all_kw2key[w] for w in target}
used_keys = set(KW.values())
KEY_META = {}
for m in manager.get_memes():
    if m.key not in used_keys:
        continue
    p = m.params_type
    args_options = []
    if p.args_type:
        for opt in p.args_type.parser_options:
            desc = "|".join(opt.names)
            if opt.args:
                desc += " " + ", ".join(a.name for a in opt.args)
            args_options.append(desc)
    KEY_META[m.key] = {
        "min_images": p.min_images, "max_images": p.max_images,
        "min_texts": p.min_texts, "max_texts": p.max_texts,
        "default_texts": list(p.default_texts),
        "args_options": args_options,
    }

head = (
    "# -*- coding: utf-8 -*-\n"
    '"""meme 关键词静态数据（由 build_data_whitelist.py 生成，勿手改；可用「meme更新」重建）。\n\n'
    "KW: 关键词 -> 唯一对应的 meme 模板 key\n"
    "META: key -> {min_images,max_images,min_texts,max_texts,default_texts,args_options}\n"
    '仅收录能唯一映射到单个 key 的关键词，避免歧义触发。\n'
    '"""\n'
)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(head)
    f.write("KW = %s\n" % json.dumps(KW, ensure_ascii=False))
    f.write("META = %s\n" % json.dumps(KEY_META, ensure_ascii=False))

print("完成: KW=%d META=%d" % (len(KW), len(KEY_META)))
print("含肥鱼说:", "肥鱼说" in KW, "| 含符箓:", "符箓" in KW)