# -*- coding: utf-8 -*-
"""今日运势签到：复用 astrbot_plugin_jrys 的签到/等级/运势逻辑，改用 PIL 绘图卡。

卡片背景支持（由 settings.json 的 jrys_background 控制，可选）：
  · 空/留空  → 内置随机背景图 API（南风风景，直出图）
  · 公网 URL → 直接用该图
  · 本地路径 → 本地单图 / 本地目录（随机挑一张）
取图失败时回退到自绘渐变底，不影响出卡。

卡片寄语默认取「随机一言」API（api.sretna.cn/api/aword/auto）。

命令：
  今日运势 / 运势 / 今日签运   签到并返回本日运势卡片
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import pathlib
import random
import time
from datetime import date, datetime, time as dtime

import aiohttp

from config import ROOT
from bot.core.sender import FT_IMAGE
from bot.core import wallet
from bot.commands import register, ROLE_ALL

_log = logging.getLogger("jrys")

_DATA_DIR = os.path.join(ROOT, "data", "jrys")
_DATA_FILE = os.path.join(_DATA_DIR, "jrys_data.json")
_TMP = os.path.join(ROOT, "tmp", "jrys")
os.makedirs(_TMP, exist_ok=True)

# ---------- 常量（来自原插件）----------
SEED_MOD = 1_000_000_001
CARD_W, CARD_H = 600, 560
_DEFAULT_BG_API = "https://api.sretna.cn/api/scenery/auto"
_DEFAULT_QUOTE_API = "https://api.sretna.cn/api/aword/auto"
_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
}

DEFAULT_LEVELS = [
    {"level": 0, "levelExp": 0, "levelName": "不知名杂鱼", "levelColor": (131, 131, 131)},
    {"level": 1, "levelExp": 500, "levelName": "荒野漫步者", "levelColor": (131, 131, 131)},
    {"level": 2, "levelExp": 1000, "levelName": "拓荒者", "levelColor": (131, 131, 131)},
    {"level": 3, "levelExp": 1500, "levelName": "冒险家", "levelColor": (131, 131, 131)},
    {"level": 4, "levelExp": 2000, "levelName": "传说的冒险家", "levelColor": (0, 0, 0)},
    {"level": 5, "levelExp": 3000, "levelName": "隐秘收藏家", "levelColor": (0, 0, 0)},
    {"level": 6, "levelExp": 4000, "levelName": "言灵探索者", "levelColor": (66, 188, 5)},
    {"level": 7, "levelExp": 5000, "levelName": "水系魔法师", "levelColor": (66, 188, 5)},
    {"level": 8, "levelExp": 6000, "levelName": "水系魔导师", "levelColor": (66, 188, 5)},
    {"level": 9, "levelExp": 8000, "levelName": "藏书的魔女", "levelColor": (32, 3, 218)},
    {"level": 10, "levelExp": 10000, "levelName": "人形图书馆", "levelColor": (32, 3, 218)},
    {"level": 11, "levelExp": 15000, "levelName": "文明归档员", "levelColor": (32, 3, 218)},
    {"level": 12, "levelExp": 20000, "levelName": "高塔思索者", "levelColor": (3, 164, 218)},
    {"level": 13, "levelExp": 25000, "levelName": "未知探索者", "levelColor": (3, 164, 218)},
    {"level": 14, "levelExp": 30000, "levelName": "背负真相之人", "levelColor": (157, 3, 218)},
    {"level": 15, "levelExp": 35000, "levelName": "守密人", "levelColor": (157, 3, 218)},
    {"level": 16, "levelExp": 40000, "levelName": "被缚的倒吊者", "levelColor": (157, 3, 218)},
    {"level": 17, "levelExp": 45000, "levelName": "崩毁世界之人", "levelColor": (241, 1, 113)},
    {"level": 18, "levelExp": 50000, "levelName": "命运眷顾者", "levelColor": (241, 1, 113)},
    {"level": 19, "levelExp": 100000, "levelName": "文明领航员", "levelColor": (201, 184, 109)},
    {"level": 20, "levelExp": 1000000, "levelName": "天选之人", "levelColor": (255, 208, 0)},
]

DEFAULT_FORTUNES = [
    (0, "走平坦的路但会摔倒的程度"), (5, "吃泡面会没有调味包的程度"),
    (15, "上厕所会忘记带纸的程度"), (20, "上学/上班路上会堵车的程度"),
    (25, "点外卖很晚才会送到的程度"), (30, "点外卖会多给予赠品的程度"),
    (35, "出门能捡到几枚硬币的程度"), (40, "踩到香蕉皮不会滑倒的程度"),
    (50, "玩滑梯能流畅滑到底的程度"), (60, "晚上走森林不会迷路的程度"),
    (70, "打游戏能够轻松过关的程度"), (80, "抽卡能够大成功的程度"),
    (95, "天选之人"),
]

DEFAULT_EVENTS = [
    ("看直播", "喜欢的内容开播啦", "喜欢的内容咕了一整天"),
    ("打轴", "一次性过", "谁说话这么难懂"), ("剪辑", "灵感爆发", "一团乱麻"),
    ("校对", "变成无情的审轴机器", "被闪轴闪瞎眼"),
    ("背单词", "这次六级肯定过", "背完50个忘了45个"),
    ("做作业", "做的每个都对", "做一个做错一个"),
    ("锻炼身体", "身体健康，更加精神", "容易用力过猛"),
    ("烹饪", "味道意外不错", "难道这就是仰望星空派"),
    ("告白", "其实我也喜欢你好久了", "对不起，你是一个好人"),
    ("追新番", "正好看到精彩回", "可能被剧透"),
    ("音游", "手感在线", "又双叒叕 LOST 了"),
    ("向大佬请教", "太棒了，学到许多", "太棒了，什么都没学到"),
    ("早起", "迎接第一缕阳光", "才4点，再睡一会"), ("早睡", "第二天精神饱满", "失眠数羊画圈圈"),
    ("抽卡", "单抽出货", "到井前一发出货"), ("拼乐高", "顺利完工", "发现少了一块零件"),
    ("跳槽", "新工作待遇大幅提升", "待遇还不如之前的"),
    ("写开源库", "代码写得又快又稳", "写完发现已有更好的轮子"),
    ("写单元测试", "将减少出错", "会降低你的开发效率"),
    ("白天上线", "今天白天上线是安全的", "可能导致灾难性后果"),
    ("重构", "代码质量得到提高", "很可能陷入泥潭"),
    ("面试", "面试官今天心情很好", "面试官不爽，会拿你出气"),
    ("提交代码", "遇到冲突的几率最低", "会遇到一大堆冲突"),
    ("代码复审", "发现重要问题的几率大大增加", "你什么问题都发现不了"),
    ("晚上上线", "晚上是精神最好的时候", "你白天已经筋疲力尽了"),
    ("氪金", "早买早享受", "第二天就 50% off"),
    ("挑战高难", "一上来就是新纪录", "先热手比较好"),
    ("与群友水聊", "话题不断", "容易聊到忘记正事"),
    ("学习新技能", "有会成为大神的资质", "可能会误入歧途"),
    ("上课玩手机", "会发现好玩的事情", "会被老师教训"),
    ("出门带伞", "今天下雨你信不信", "好运气都被遮住了"),
    ("玩 Minecraft", "建筑灵感爆发", "启动器可能闹脾气"),
    ("上 Steam", "愿望单迎来折扣", "钱包会被清空"), ("修图", "原片直出毫无压力", "Photoshop 未响应"),
    ("赶稿", "完美守住 deadline", "终究还是超期"),
    ("摸鱼", "短暂恢复精神", "被老板当场抓获"),
    ("入手新游戏", "你会玩的很开心", "这游戏明天就打折"),
    ("出门", "今天会是个好天气", "中途可能变天"),
]


# ---------- 运势算法（原插件逻辑）----------
def stable_user_number(uid: str) -> int:
    uid = str(uid)
    if uid.isdigit():
        return int(uid)
    return int(hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16], 16)


def seeded_random(seed: int) -> float:
    v = math.sin(seed) * 10000
    return v - math.floor(v)


def _today_midnight() -> int:
    return int(datetime.combine(date.today(), dtime.min).timestamp())


def get_fortune(uid: str) -> int:
    user_number = stable_user_number(uid)
    seed = (user_number * _today_midnight()) % SEED_MOD
    return int(seeded_random(seed) * 100)


def get_random_events(uid: str) -> list:
    seed = get_fortune(uid)
    indexes, seen, counter = [], set(), 0
    while len(indexes) < 4:
        idx = math.floor(seeded_random(seed + counter) * len(DEFAULT_EVENTS))
        if idx not in seen:
            indexes.append(idx)
            seen.add(idx)
        counter += 1
    return [DEFAULT_EVENTS[i] for i in indexes]


def random_with_luck(min_v: int, max_v: int, luck: int) -> int:
    low, high = sorted((min_v, max_v))
    mean, std = luck / 100, 0.12
    a = b = 0.0
    while a == 0.0 or b == 0.0:
        a, b = random.random(), random.random()
    v = math.cos(2 * math.pi * a) * math.sqrt(-2 * math.log(b))
    v = v * std + mean
    if v > 1:
        v = 2 - v
    elif v < 0:
        v = -v
    v = max(0, min(1, v))
    return round(v * (high - low) + low)


def get_level_info(exp: int):
    current, next_exp = DEFAULT_LEVELS[0], None
    for index, level in enumerate(DEFAULT_LEVELS):
        if exp >= level["levelExp"]:
            current = level
            next_exp = DEFAULT_LEVELS[index + 1]["levelExp"] if index + 1 < len(DEFAULT_LEVELS) else None
        else:
            break
    return current, next_exp


def get_fortune_desc(luck: int) -> str:
    desc = DEFAULT_FORTUNES[0][1]
    for threshold, d in DEFAULT_FORTUNES:
        desc = d if luck >= threshold else desc
        if luck < threshold:
            break
    return desc


# ---------- 用户数据 ----------
def load_data() -> dict:
    if not os.path.exists(_DATA_FILE):
        return {}
    try:
        with open(_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data: dict):
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _DATA_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _DATA_FILE)
    except Exception:
        pass


def signin_user(uid: str, username: str) -> dict:
    """签到：当天已签返回 status=1，否则结算经验/喵币并写盘。"""
    data = load_data()
    today = date.today().isoformat()
    user = data.get(uid)
    if not user:
        user = {"name": username, "last_signin": "", "exp": 0, "signin_count": 0}
        data[uid] = user
    if user.get("last_signin") == today:
        return {"status": 1, "exp_gain": 0, "coin_gain": 0,
                "today_coin": int(user.get("last_coin_gain", 0)),
                "total_exp": int(user.get("exp", 0)),
                "signin_count": int(user.get("signin_count", 0))}
    luck = get_fortune(uid)
    exp_gain = random_with_luck(1, 100, luck)
    if random.random() < 0.02:      # 极低概率：1000-3000
        coin_gain = random.randint(1000, 3000)
    else:                            # 常规：0-1000
        coin_gain = random.randint(0, 1000)
    user["name"] = username
    user["last_signin"] = today
    user["exp"] = int(user.get("exp", 0)) + exp_gain
    user["last_coin_gain"] = coin_gain
    user["signin_count"] = int(user.get("signin_count", 0)) + 1
    save_data(data)
    wallet.add(uid, coin_gain)
    return {"status": 0, "exp_gain": exp_gain, "coin_gain": coin_gain,
            "today_coin": coin_gain,
            "total_exp": user["exp"], "signin_count": user["signin_count"]}


# ---------- 背景 / 寄语 ----------
def advertised_fortune_quote(text: str) -> str:
    return text or "『 随机生成，请勿迷信。』"


async def _http_text(url: str) -> str | None:
    try:
        async with aiohttp.ClientSession(headers=_HDRS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=12), ssl=False) as r:
                if r.status != 200:
                    return None
                return (await r.text()).strip() or None
    except Exception:
        return None


async def _http_bytes(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession(headers=_HDRS) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


def _background_source() -> str:
    """返回背景来源字符串：空=默认API，http=URL，否则为本地路径/目录。"""
    try:
        from config import _cfg
        return str(_cfg("JRYS_BACKGROUND", "")).strip()
    except Exception:
        return ""


async def fetch_background_bytes() -> bytes | None:
    """按配置取背景图字节。返回 None 时由调用方回退渐变。"""
    src = _background_source()
    if src.startswith("http://") or src.startswith("https://"):
        return await _http_bytes(src)
    if not src:
        return await _http_bytes(_DEFAULT_BG_API)
    # 本地路径 / 目录
    p = pathlib.Path(src)
    if not p.is_absolute():
        p = pathlib.Path(ROOT) / p
    try:
        if p.is_dir():
            imgs = [x for x in p.iterdir()
                    if x.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}]
            p = random.choice(imgs) if imgs else None
        if p and p.is_file():
            with open(p, "rb") as f:
                return f.read()
    except Exception:
        return None
    return None


async def fetch_quote() -> str:
    text = await _http_text(_DEFAULT_QUOTE_API)
    return advertised_fortune_quote(text)


# ---------- PIL 绘图 ----------
def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = []
    if bold:
        candidates += [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"]
    candidates += [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\simhei.ttc", r"C:\Windows\Fonts\Deng.ttf"]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _shorten(s: str, limit: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _draw_rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _draw_progress(draw, x0, y0, x1, h, ratio, filled, bg):
    r = h / 2
    draw.rounded_rectangle([x0, y0, x1, y0 + h], radius=r, fill=bg)
    fw = (x1 - x0) * max(0.0, min(1.0, ratio))
    if fw > r * 2:
        draw.rounded_rectangle([x0, y0, x0 + fw, y0 + h], radius=min(r, fw / 2), fill=filled)


def _cover_to_bg(bg, cw, ch):
    from PIL import Image
    w, h = bg.size
    scale = max(cw / w, ch / h)
    if abs(scale - 1.0) > 1e-6:
        bg = bg.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    bg = bg.convert("RGB")
    bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2,
                  (bg.width - cw) // 2 + cw, (bg.height - ch) // 2 + ch))
    return bg


def _gradient_bg(cw, ch, top=(120, 130, 180), bottom=(250, 245, 235)):
    from PIL import Image
    img = Image.new("RGB", (cw, ch), top)
    px = img.load()
    r0, g0, b0 = top
    r1, g1, b1 = bottom
    for y in range(ch):
        t = y / max(1, ch - 1)
        color = (int(r0 + (r1 - r0) * t), int(g0 + (g1 - g0) * t), int(b0 + (b1 - b0) * t))
        for x in range(cw):
            px[x, y] = color
    return img


def render_card(view: dict) -> str:
    """渲染运势卡为 PNG 临时文件路径。view 需含各字段。"""
    from io import BytesIO
    from PIL import Image, ImageDraw

    bg_raw = view.get("bg_bytes")
    if bg_raw:
        try:
            base = Image.open(BytesIO(bg_raw))
            base = _cover_to_bg(base, CARD_W, CARD_H)
        except Exception:
            base = _gradient_bg(CARD_W, CARD_H)
    else:
        base = _gradient_bg(CARD_W, CARD_H)

    overlay = Image.new("RGBA", (CARD_W, CARD_H), (255, 255, 255, 0))
    od = ImageDraw.Draw(overlay)
    _draw_rounded(od, [16, 16, CARD_W - 16, CARD_H - 16], 24, (255, 255, 255, 208))
    base = base.convert("RGBA")
    base = Image.alpha_composite(base, overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    black = (40, 40, 40)
    gray = (120, 120, 120)
    red = (217, 74, 63)
    blue = (30, 60, 120)

    x0 = 48
    x1 = CARD_W - 48
    y = 44

    # 标题：问候 + 用户名
    title_f = _load_font(32, bold=True)
    draw.text((x0, y), view["greeting"], font=title_f, fill=black)
    name_f = _load_font(32, bold=True)
    draw.text((x0 + 40, y + 34), f"{view['username']}", font=name_f, fill=black)
    # 日期（右上）
    date_f = _load_font(22, bold=True)
    date_w = draw.textlength(view["date"], font=date_f)
    draw.text((x1 - date_w, y + 16), view["date"], font=date_f, fill=gray)
    y += 110

    # 状态行：签到状态 / 经验积分
    status_f = _load_font(19, bold=True)
    status_text = view["status_text"]
    draw.text((x0, y), status_text, font=status_f, fill=gray)
    y += 44

    # 等级行
    lv_name, lv_color, exp_text = view["level_name"], view["level_color"], view["exp_text"]
    lv_f = _load_font(28, bold=True)
    lw = draw.textlength(lv_name, font=lv_f)
    draw.text((x0, y), lv_name, font=lv_f, fill=lv_color)
    exp_f = _load_font(20, bold=True)
    ew = draw.textlength(exp_text, font=exp_f)
    draw.text((x1 - ew, y + 6), exp_text, font=exp_f, fill=gray)
    y += 48

    # 进度条
    _draw_progress(draw, x0, y, x1, 16, view["progress"], filled=(120, 120, 120), bg=(228, 228, 228))
    y += 40

    # 今日运势
    luck_f = _load_font(30, bold=True)
    text = "今日运势 · " + str(view["luck"])
    draw.text((x0, y), text, font=luck_f, fill=black)
    desc_f = _load_font(18, bold=True)
    desc = _shorten(view["fortune_desc"], 20)
    dw = draw.textlength(desc, font=desc_f)
    draw.text((x1 - dw, y + 10), desc, font=desc_f, fill=gray)
    y += 56

    # 宜 / 忌
    for tag, color, items in (("宜", red, view["good_events"]), ("忌", blue, view["bad_events"])):
        tw = 52
        draw.ellipse([x0 - 6, y - 6, x0 - 6 + tw, y - 6 + tw], fill=color)
        draw.text((x0, y - 4), tag, font=_load_font(22, bold=True), fill=(255, 255, 255))
        item_f = _load_font(20)
        yy = y + 6
        for name, good in items:
            line = _shorten(f"{name}——{good}", 26)
            draw.text((x0 + 60, yy), line, font=item_f, fill=(70, 70, 70))
            yy += 34
        y += 6 + 4 + 68

    y += 8
    # 分隔线
    draw.line((x0, y, x1, y), fill=(210, 210, 210), width=2)
    y += 10  # 分隔线 → 寄语间距收紧，让寄语上移约半行字高，避免贴底出框

    # 寄语
    quote_f = _load_font(18, bold=True)
    quote = view["quote"]
    quote = _shorten(quote, 26)
    qw = draw.textlength(quote, font=quote_f)
    draw.text(((CARD_W - qw) / 2, y), quote, font=quote_f, fill=(150, 150, 150))
    y += 34

    out = os.path.join(_TMP, f"{int(time.time() * 1000)}_{random.getrandbits(16)}.png")
    base.save(out, format="PNG")
    return out


# ---------- 命令 ----------
def _jrys_matcher(t):
    return (t or "").strip() in ("今日运势", "运势", "今日签运")


def _sender_name(ctx) -> str:
    """取发送者 QQ 昵称。群聊昵称存放位置与 botpy 官方一致：作者（author）的 username 字段。"""
    author = getattr(ctx.message, "author", None)
    if isinstance(author, dict):
        for k in ("username", "member_name", "user_name", "nickname"):
            v = author.get(k)
            if v:
                return str(v).strip()
        return ""
    try:
        name = getattr(author, "username", None) or getattr(author, "member_name", None) \
            or getattr(author, "user_name", None) or getattr(author, "nickname", None)
    except Exception:
        name = None
    return (name or "").strip()


@register(keywords=["今日运势", "运势", "今日签运"], help="今日运势签到喵", matcher=_jrys_matcher, role=ROLE_ALL, exact=True)
async def cmd_jrys(ctx):
    uid = ctx.openid or "anonymous"
    username = _sender_name(ctx) or f"用户{uid[-4:]}"

    async def _run():
        signin = signin_user(uid, username)
        if signin["status"] == 1:
            return await ctx.reply_text("今天签过到了喵，明天再来吧~")
        luck = get_fortune(uid)
        [quote, bg] = await asyncio.gather(fetch_quote(), fetch_background_bytes())

        status_text = f"签到成功  ·  经验 +{signin['exp_gain']}  ·  今日喵币 +{signin['coin_gain']}"

        total_exp = signin["total_exp"]
        level, next_exp = get_level_info(total_exp)
        exp_text = f"{total_exp}/??? " if next_exp is None else f"{total_exp}/{next_exp}"
        progress = 1.0 if next_exp is None else min(total_exp / next_exp, 1.0)

        events = get_random_events(uid)
        now = datetime.now()
        view = {
            "greeting": _greeting(now.hour),
            "username": _shorten(username, 12),
            "date": f"{now.month:02d}/{now.day:02d}",
            "status_text": status_text,
            "level_name": level["levelName"],
            "level_color": level["levelColor"],
            "exp_text": exp_text,
            "progress": progress,
            "luck": luck,
            "fortune_desc": get_fortune_desc(luck),
            "good_events": [(e[0], e[1]) for e in events[:2]],
            "bad_events": [(e[0], e[2]) for e in events[2:]],
            "quote": quote,
            "bg_bytes": bg,
        }
        path = await asyncio.to_thread(render_card, view)
        try:
            res = await ctx.sender.send_local_file(ctx.message, FT_IMAGE, path, reply=False)
            if isinstance(res, str) and res.startswith("发送失败"):
                await ctx.reply(f"{res}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    await _run()


def _greeting(hour: int) -> str:
    if hour < 5:
        return "晚安"
    if hour < 9:
        return "早上好"
    if hour < 11:
        return "上午好"
    if hour < 14:
        return "中午好"
    if hour < 18:
        return "下午好"
    if hour < 20:
        return "傍晚好"
    return "晚上好"


# web 后台「其他功能」模块分组用（见 bot/core/webui.py 的 _module_groups）
JRESY_CMD_NAMES = {"cmd_jrys"}