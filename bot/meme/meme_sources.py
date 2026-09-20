# -*- coding: utf-8 -*-
"""统一的 meme 模板源装载器。

背景（为什么需要这个模块）
--------------------------------------------------------------
`qq-cat-memes` 聚合仓库通过 git 子模块引用 4 个上游源仓库，但**它们的目录布局并不统一**：

| 源仓库                     | 模板所在目录 |
| -------------------------- | ------------ |
| `meme_emoji`               | `emoji/`     |
| `crazy_emoji`              | `emoji/`     |
| `meme-generator-contrib`   | `memes/`     |
| `meme-demo`                | `memes/`     |

早期安装脚本写的是「找 `memes/`，找不到就退回仓库根目录再扫一层」。
对 `emoji/` 布局的仓库，这会**静默捞到 0 个模板**（实测 465 个模板里丢掉 445 个）。

本模块改用「**按内容自适应**」而不是「按目录名猜」：
只要一个目录里**直接含有 `__init__.py` 子目录**，就认为它是模板容器。

用法
--------------------------------------------------------------
    from bot.meme.meme_sources import iter_template_dirs, collect_sources

    # 枚举某个源仓库下的所有模板目录
    for d in iter_template_dirs(Path("bot/meme/custom_memes/_sources/meme_emoji")):
        ...

    # 直接从多个源根目录收集（去重）
    tmpls = collect_sources([Path(src) for src in source_roots])
"""

import os
import shutil
from pathlib import Path

# 候选的模板容器目录名，按优先级排列。
# 先试这些，命中就不必深入递归，避免把 docs/ 里的示例也当成模板。
_PREFERRED_CONTAINERS = ("memes", "emoji")

# 递归搜索时跳过的目录名（文档、缓存、版本控制等，里面不会是真模板）
_SKIP_DIRS = {
    ".git", "__pycache__", "docs", "doc", ".github", "node_modules",
    "resources", "images", "test", "tests", ".venv", "venv",
}


def is_template_dir(path: Path) -> bool:
    """目录是不是一个 meme 模板？

    判据与 meme-generator 的约定一致：目录下直接有 `__init__.py`。
    再加一层保险：排除 `__pycache__` 这类同名噪声。
    """
    if not path.is_dir():
        return False
    if path.name.startswith(".") or path.name == "__pycache__":
        return False
    return (path / "__init__.py").is_file()


def _iter_direct_template_children(container: Path):
    """列出一个「容器目录」下所有模板子目录。"""
    try:
        children = sorted(container.iterdir())
    except (OSError, PermissionError):
        return
    for child in children:
        if is_template_dir(child):
            yield child


def _find_containers(root: Path, max_depth: int = 4):
    """在 root 下按优先级找出所有「模板容器目录」。

    策略：
      1. root 自身若是容器（直接含模板子目录）→ 用它
      2. 否则优先尝试已知容器名（memes / emoji）
      3. 都没有时，浅层递归找第一个含模板的目录
    """
    if not root.is_dir():
        return

    # 1) root 自己就是容器吗？
    direct = list(_iter_direct_template_children(root))
    if direct:
        yield root
        return

    # 2) 已知的容器名
    for name in _PREFERRED_CONTAINERS:
        cand = root / name
        if cand.is_dir() and list(_iter_direct_template_children(cand)):
            yield cand

    # 有命中的就不再递归，避免重复
    if any((root / n).is_dir() and list(_iter_direct_template_children(root / n))
           for n in _PREFERRED_CONTAINERS):
        return

    # 3) 浅层递归兜底（应对未来出现的新布局）
    root_depth = len(root.parts)
    for cur, dirs, _files in os.walk(root):
        cur_path = Path(cur)
        depth = len(cur_path.parts) - root_depth
        if depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        if cur_path == root:
            continue
        if list(_iter_direct_template_children(cur_path)):
            yield cur_path
            dirs[:] = []  # 命中即止，不再深入


def iter_template_dirs(source_root: Path):
    """枚举单个源仓库根目录下的全部模板目录（去重、保序）。"""
    source_root = Path(source_root)
    if not source_root.is_dir():
        return
    seen = set()
    for container in _find_containers(source_root):
        for tpl in _iter_direct_template_children(container):
            key = tpl.name
            if key in seen:
                continue
            seen.add(key)
            yield tpl


def collect_sources(source_roots):
    """从多个源根目录收集模板，返回 {模板名: 源路径}（先出现的优先）。

    后出现的同名模板不会覆盖先出现的 —— 由调用方决定「谁优先」，
    把更权威的源放在列表前面即可。
    """
    found = {}
    for root in source_roots:
        for tpl in iter_template_dirs(Path(root)):
            found.setdefault(tpl.name, tpl)
    return found


def default_source_roots(custom_dir=None, sources_dir=None):
    """返回默认要扫描的源根目录列表（顺序 = 优先级）。

    `_sources/` 下的每个子目录是一个源仓库。顺序固定成：
      meme_emoji -> crazy_emoji -> meme-generator-contrib -> meme-demo
    这样同名模板以 meme_emoji 为准（它的模板量最大、最常用）。
    其余未列出的目录按名称排在后面。

    sources_dir 可显式指定 `_sources` 目录（便于测试/自定义布局）；
    不传时取 `custom_dir/_sources`。
    """
    if sources_dir is not None:
        src = Path(sources_dir)
    else:
        custom_dir = Path(custom_dir) if custom_dir else Path(__file__).resolve().parent / "custom_memes"
        src = custom_dir / "_sources"
    if not src.is_dir():
        return []

    order = ["meme_emoji", "crazy_emoji", "meme-generator-contrib", "meme-demo"]
    try:
        present = [d for d in src.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except (OSError, PermissionError):
        return []

    roots = [src / n for n in order if (src / n).is_dir()]
    extra = sorted(d for d in present if d.name not in order)
    return roots + extra


def sync_sources(custom_dir=None, sources_dir=None, dry_run=False, verbose=True):
    """把 `_sources/` 里的模板装载（复制）进 `custom_memes/`。

    策略：**仓库优先 + 保留本地独有**
      · 同名模板 → 用源仓库的版本覆盖（仓库是权威来源）
      · 仓库里没有的（项目自带的 `feiyu`、手工补的额外模板）→ 原样保留

    返回 (copied, skipped, kept_local, total_source)。
    """
    custom_dir = Path(custom_dir) if custom_dir else Path(__file__).resolve().parent / "custom_memes"
    custom_dir.mkdir(parents=True, exist_ok=True)

    roots = default_source_roots(custom_dir, sources_dir)
    if not roots:
        if verbose:
            print("[meme_sources] 未找到 _sources/，跳过装载")
        return 0, 0, 0, 0

    if verbose:
        print("[meme_sources] 扫描 %d 个源仓库：" % len(roots))
        for r in roots:
            print("    %s -> %d 个模板" % (r.name, len(list(iter_template_dirs(r)))))

    templates = collect_sources(roots)
    if verbose:
        print("[meme_sources] 源仓库模板总数（去重后）: %d" % len(templates))

    copied = skipped = 0
    for name, src_path in sorted(templates.items()):
        dst = custom_dir / name
        if dst.is_dir() and _same_content(src_path, dst):
            skipped += 1
            continue
        if dry_run:
            copied += 1
            continue
        try:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src_path, dst)
            copied += 1
        except (OSError, shutil.Error) as e:
            if verbose:
                print("    [warn] 复制失败 %s: %s" % (name, e))

    # 数一数本地独有（非 _sources 提供）的模板
    local_only = 0
    for child in custom_dir.iterdir():
        if not child.is_dir() or child.name == "_sources" or child.name.startswith("."):
            continue
        if child.name not in templates and is_template_dir(child):
            local_only += 1

    if verbose:
        print("[meme_sources] 装载完成: 新复制/覆盖 %d，已是最新 %d，本地独有保留 %d"
              % (copied, skipped, local_only))
        total = sum(1 for c in custom_dir.iterdir()
                    if is_template_dir(c) and c.name != "_sources")
        print("[meme_sources] custom_memes 现有可用模板: %d" % total)

    return copied, skipped, local_only, len(templates)


def _same_content(a: Path, b: Path) -> bool:
    """粗判两个模板目录内容是否一致（比文件数与总字节数）。"""
    def sig(p: Path):
        n = 0
        size = 0
        for cur, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                n += 1
                try:
                    size += (Path(cur) / f).stat().st_size
                except OSError:
                    pass
        return n, size

    try:
        return sig(a) == sig(b)
    except OSError:
        return False


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="把 _sources 里的 meme 模板装载进 custom_memes")
    ap.add_argument("--custom-dir", default=None, help="custom_memes 目录（默认取本模块同级）")
    ap.add_argument("--sources-dir", default=None, help="_sources 目录（默认取 custom-dir/_sources）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不实际复制")
    args = ap.parse_args()
    sync_sources(custom_dir=args.custom_dir, sources_dir=args.sources_dir,
                 dry_run=args.dry_run)
