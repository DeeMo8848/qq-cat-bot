# -*- coding: utf-8 -*-
"""「吃什么」随机食物推荐（移植 astrbot_plugin_what_to_eat 并扩展增删管理）。

移植并保留的能力（配置见 config.py 的 EAT_*，可覆盖至 settings.json）：
  · 自定义关键词   触发词可配置多个（EAT_TRIGGER_KEYWORDS）
  · 智能识别       开启后关键词出现在文本任意位置即触发（EAT_SMART_CONTAINS）
  · 随机推荐       按概率随机推荐 or 复读「是啊，吃什么」（EAT_RECOMMEND_PROBABILITY）
  · 可编辑食物库   内置 50 个食物可整体覆盖（EAT_BUILTIN_FOODS / EAT_USE_BUILTIN）
  · 自定义扩展     额外的自定义食物列表（EAT_CUSTOM_FOODS）
  · 食物配图       EAT_FOOD_IMAGES_DIR 目录里放 "{食物}.jpg" 即可推荐时图文同发
                  （未配置时默认用项目内 resources/food_images，增食物附带的图会存这里）
  · 频率限制       每分钟最多回复 N 次，超了静默跳过，防多 Bot 循环（EAT_RATE_LIMIT_*）
  · 复读冷却       复读后一段时间强制改为推荐，避免一直复读（EAT_ECHO_COOLDOWN_*）

扩展的命令：
  · 添加食物 xx  —— 存入全部食物；已存在则提醒不加。附带/引用图片时把图存为该食物配图
                    （同名文件自动编号为 xx-1/xx-2…，抽取时随机取）
  · 移除食物 xx  —— 移除该食物及其所有配图
  · 食物列表     —— 文字列出当前全部食物
运行时增删持久化到 bot/data/eat_store.json，重启不丢。
「参数兼容」针对 AstrBot 的多 handler 调用，本项目无此概念，不适用。
"""

import json
import os
import random
import re
import time
from collections import deque

import aiohttp

from config import (
    ROOT,
    EAT_TRIGGER_KEYWORDS, EAT_SMART_CONTAINS, EAT_RECOMMEND_PROBABILITY,
    EAT_USE_BUILTIN, EAT_BUILTIN_FOODS, EAT_CUSTOM_FOODS,
    EAT_RATE_LIMIT_ENABLED, EAT_RATE_LIMIT_MAX,
    EAT_ECHO_COOLDOWN_ENABLED, EAT_ECHO_COOLDOWN_SECONDS,
    EAT_FOOD_IMAGES_DIR,
)
from bot.commands import register, ROLE_ALL

# 供 Web 后台「其他功能 → 吃什么」插件总开关使用的命令名集合
EAT_CMD_NAMES = {"cmd_eat", "cmd_add_food", "cmd_remove_food", "cmd_food_list"}

_ECHO = "是啊，吃什么"
_TEMPLATES = [
    "要不吃{food}？", "试试{food}吧！", "{food}怎么样？",
    "推荐你吃{food}！", "今天吃{food}吧！", "{food}了解一下？",
]
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".apng", ".bmp"}
_DEFAULT_IMG_DIR = os.path.join(ROOT, "resources", "food_images")
_EAT_STORE_PATH = os.path.join(ROOT, "bot", "data", "eat_store.json")
_TIMEOUT = aiohttp.ClientTimeout(total=20)

# 频率限制窗口（秒）与调用时间戳队列
_RATE_WINDOW = 60.0
_hits: deque = deque()
_echo_at = 0.0  # 最近一次复读的时间戳


# ---------- 持久化/路径 ----------

def _images_dir() -> str:
    """配图目录：优先用户配置，否则默认项目内目录。"""
    d = EAT_FOOD_IMAGES_DIR.strip() if EAT_FOOD_IMAGES_DIR else ""
    return d if d else _DEFAULT_IMG_DIR


def _load_store() -> dict:
    try:
        with open(_EAT_STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_store(store: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_EAT_STORE_PATH), exist_ok=True)
        with open(_EAT_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------- 食物池 ----------

def _food_pool():
    """当前全部食物：内置 + 自定义 + 运行时新增，去掉被移除的。"""
    store = _load_store()
    pool = []
    if EAT_USE_BUILTIN:
        pool.extend(EAT_BUILTIN_FOODS or [])
    pool.extend(EAT_CUSTOM_FOODS or [])
    pool.extend(store.get("added", []))
    removed = set(store.get("removed", []) or [])
    out = list(dict.fromkeys(f for f in pool if f and f.strip()))
    return [f for f in out if f not in removed]


def _food_exists(food: str) -> bool:
    return food in _food_pool()


# ---------- 配图 ----------

def _match_food_filename(filename: str, food: str) -> bool:
    """文件名是否属于该食物：形如 {food}.jpg、{food}-1.jpg、{food}_2.png。"""
    stem = os.path.splitext(os.path.basename(filename))[0].strip().lower()
    base = food.strip().lower()
    if not base:
        return False
    if stem == base:
        return True
    return stem.startswith(base) and stem[len(base):len(base) + 1] in "-_ "


def _pick_food_image(food: str):
    """在配图目录里随机取该食物的一张图；没有返回 None。"""
    d = _images_dir()
    if not os.path.isdir(d):
        return None
    matches = []
    try:
        for name in os.listdir(d):
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            if os.path.splitext(name)[1].lower() in _IMG_EXTS and _match_food_filename(name, food):
                matches.append(path)
        matches.sort()
    except Exception:
        return None
    return random.choice(matches) if matches else None


def _next_image_path(food: str, ext: str) -> str:
    """为食物生成一个不冲突的配图文件路径：优先 {food}{ext}，占用则 {food}-1{ext}、-2…"""
    d = _images_dir()
    os.makedirs(d, exist_ok=True)
    candidate = os.path.join(d, food.strip() + ext)
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        candidate = os.path.join(d, "{}-{}{}".format(food.strip(), n, ext))
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _remove_food_images(food: str) -> int:
    """删除该食物的所有配图，返回删除数量。"""
    d = _images_dir()
    if not os.path.isdir(d):
        return 0
    removed = 0
    try:
        for name in os.listdir(d):
            if _match_food_filename(name, food):
                try:
                    os.remove(os.path.join(d, name))
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return removed


async def _download_image(url: str) -> tuple[bytes, str] | None:
    """下载图片，返回 (字节, 扩展名)。"""
    ext = ".jpg"
    try:
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(url).path)
        p = os.path.splitext(path)[1].lower()
        if p in _IMG_EXTS:
            ext = p
    except Exception:
        pass
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read(), ext
    except Exception:
        return None


# ---------- 触发与逻辑 ----------

def _matcher(t):
    """触发「吃什么」：默认精确匹配任一关键词；开启智能识别后按包含匹配。"""
    t = (t or "").strip()
    if not t:
        return False
    if EAT_SMART_CONTAINS:
        return any(k in t for k in EAT_TRIGGER_KEYWORDS)
    return t in EAT_TRIGGER_KEYWORDS


def _rate_limited() -> bool:
    if not EAT_RATE_LIMIT_ENABLED:
        return False
    now = time.monotonic()
    while _hits and now - _hits[0] > _RATE_WINDOW:
        _hits.popleft()
    if len(_hits) >= max(1, EAT_RATE_LIMIT_MAX):
        return True
    return False


@register(keywords=EAT_TRIGGER_KEYWORDS, help="随机推荐今天吃什么喵", matcher=_matcher, role=ROLE_ALL)
async def cmd_eat(ctx):
    global _echo_at
    if _rate_limited():
        return
    _hits.append(time.monotonic())

    pool = _food_pool()
    if not pool:
        await ctx.reply("还没有食物喵，用「添加食物 名字」加一个吧")
        return
    forced_recommend = (
        EAT_ECHO_COOLDOWN_ENABLED
        and _echo_at
        and (time.monotonic() - _echo_at) < EAT_ECHO_COOLDOWN_SECONDS
    )
    if random.random() < EAT_RECOMMEND_PROBABILITY or forced_recommend:
        food = random.choice(pool)
        img = _pick_food_image(food)
        try:
            if img:
                await ctx.sender.send_image_with_text(ctx.message, food, img, reply=False)
            else:
                await ctx.reply(random.choice(_TEMPLATES).format(food=food))
        except Exception:
            await ctx.reply(random.choice(_TEMPLATES).format(food=food))
    else:
        _echo_at = time.monotonic()
        await ctx.reply(_ECHO)


# ---------- 管理命令（添加 / 移除 / 列表） ----------

@register(keywords=["添加食物"], help="添加食物（添加食物 xx）", role=ROLE_ALL)
async def cmd_add_food(ctx):
    food = (getattr(ctx, "args", "") or "").strip()
    if not food:
        await ctx.reply("用法：添加食物 <食物名>，可随消息附一张/多张图片作为配图喵")
        return
    if _food_exists(food):
        await ctx.reply(f"「{food}」已经添加过了喵")
        return

    # 附带/引用图片 → 存为该食物配图
    imgs = getattr(ctx.message, "image_urls", None) or []
    img_result = ""
    saved = 0
    for url in imgs:
        got = await _download_image(url)
        if not got:
            continue
        data, ext = got
        try:
            path = _next_image_path(food, ext)
            with open(path, "wb") as f:
                f.write(data)
            saved += 1
        except Exception:
            pass
    if imgs and saved == 0:
        img_result = "，但图片下载失败"
    elif saved:
        img_result = f"，已保存 {saved} 张配图"

    store = _load_store()
    added = store.get("added", [])
    if food not in added:
        added.append(food)
    store["added"] = added
    _save_store(store)
    await ctx.reply(f"已添加食物「{food}」{img_result}喵")


@register(keywords=["移除食物"], help="移除食物及配图（移除食物 xx）", role=ROLE_ALL)
async def cmd_remove_food(ctx):
    food = (getattr(ctx, "args", "") or "").strip()
    if not food:
        await ctx.reply("用法：移除食物 <食物名>，会同时删除它的所有配图喵")
        return
    if not _food_exists(food):
        await ctx.reply(f"没有「{food}」这个食物喵")
        return
    n = _remove_food_images(food)
    store = _load_store()
    store["added"] = [f for f in store.get("added", []) if f != food]
    removed = store.setdefault("removed", [])
    if food not in removed:
        removed.append(food)
    _save_store(store)
    img_txt = f"，删除了 {n} 张配图" if n else ""
    await ctx.reply(f"已移除食物「{food}」{img_txt}喵")


@register(keywords=["食物列表"], help="列出当前全部食物", exact=True, role=ROLE_ALL)
async def cmd_food_list(ctx):
    pool = _food_pool()
    if not pool:
        await ctx.reply("还没有食物喵，用「添加食物 名字」加一个吧")
        return
    lines = [f"{i}. {f}" for i, f in enumerate(pool, 1)]
    header = f"当前共 {len(pool)} 种食物喵："
    # 分块避免一条消息过长
    chunk = [header]
    for line in lines:
        if sum(len(c) for c in chunk) + len(line) > 1200:
            await ctx.reply("\n".join(chunk))
            chunk = []
        chunk.append(line)
    if chunk:
        await ctx.reply("\n".join(chunk))