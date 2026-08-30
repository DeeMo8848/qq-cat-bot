# -*- coding: utf-8 -*-
"""MC百科（MCMod）模组解析器（参考 astrbot 插件 mcmod_card 移植）。

自动识别 mcmod.cn/class/{id}.html 链接 → 爬取模组页 → 解析名称/状态/红黑票/
支持版本/标签/作者/八维评分/封面 → 本地渲染一张模组卡片图发送；
卡片渲染失败时退回纯文本摘要。
"""
import io
import re
from pathlib import Path
from typing import ClassVar

from bs4 import BeautifulSoup
from PIL import Image

from .._log import logger
from ..config import PluginConfig
from ..data import ImageContent, ParseResult, Platform
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle

# 卡片渲染复用的中文字体（解析模块自带）
_FONT = Path(__file__).resolve().parents[1] / "resources" / "HYSongYunLangHeiW-1.ttf"

_BASE = "https://www.mcmod.cn"
_CLASS_PATTERN = r"https?://(?:www\.)?mcmod\.cn/class/(?P<id>\d+)\.html"

# 八维评分中文名（与页面 data-original-title 顺序一致）
_RATING_CN = ["趣味", "难度", "稳定", "实用", "美观", "平衡", "兼容", "耐玩"]
_RATING_KEYS = ["fun", "difficulty", "stability", "practicality",
                "aesthetics", "balance", "compatibility", "durability"]
_CN_TO_EN = dict(zip(_RATING_CN, _RATING_KEYS))


class McModParser(BaseParser):
    platform: ClassVar[Platform] = Platform(name="mcmod", display_name="MC百科")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.headers.update({"Accept-Language": "zh-CN,zh;q=0.9"})

    @handle("mcmod.cn", _CLASS_PATTERN)
    async def _parse_class_url(self, searched):
        return await self._build_result(searched.group("id"))

    async def _fetch_html(self, url: str) -> str:
        try:
            async with self.session.get(url, headers=self.headers, proxy=self.proxy) as resp:
                if resp.status != 200:
                    raise ParseException(f"MC百科页面 HTTP {resp.status}")
                return await resp.text()
        except ParseException:
            raise
        except Exception as e:
            raise ParseException(f"MC百科页面请求失败: {e}")

    async def _fetch_bytes(self, url: str) -> bytes | None:
        try:
            async with self.session.get(url, headers=self.headers, proxy=self.proxy) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            return None
        return None

    async def _build_result(self, mod_id: str) -> ParseResult:
        mod_id = str(mod_id).strip()
        url = f"{_BASE}/class/{mod_id}.html"
        html = await self._fetch_html(url)
        info = _parse_mod_html(html)
        title = (
            (info.get("name") or {}).get("chinese-name")
            or (info.get("name") or {}).get("english-name")
            or f"模组 {mod_id}"
        )
        if not title and not info.get("status"):
            raise ParseException("未找到该模组信息（链接无效或页面结构已变化）")

        author = self.create_author(title)
        try:
            cover = None
            cover_url = (info.get("cover") or "").split("@")[0]
            if cover_url:
                cover = await self._fetch_bytes(cover_url)
            out = self.cfg.cache_dir / f"mcmod_{mod_id}.png"
            await self._render_card(info, cover, out)
            if out.exists():
                return self.result(
                    title=title,
                    text=f"{title}\n🔗 {url}",
                    url=url,
                    author=author,
                    contents=[ImageContent(out)],
                )
        except Exception as e:
            logger.warning(f"[parse] MCMod 卡片渲染失败: {e}")

        # 兜底：纯文本摘要
        return self.result(
            title=title,
            text=_info_text(info, mod_id, url),
            url=url,
            author=author,
        )

    async def _render_card(self, info: dict, cover_bytes: bytes | None, out: Path) -> None:
        """在事件循环外渲染卡片图并保存到 out。"""
        import asyncio
        await asyncio.to_thread(_make_card, info, cover_bytes, str(out))


# --------------------------------------------------------------------------- #
# 页面解析（对应 mcmod_card 的 ModInfoParser）
# --------------------------------------------------------------------------- #
def _parse_mod_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info: dict = {}
    info.update(_parse_title(soup))
    info.update(_parse_tags(soup))
    info.update(_parse_votes(soup))
    info.update(_parse_view_count(soup))
    info.update(_parse_mc_versions(soup))
    info.update(_parse_modpack_count(soup))
    info.update(_parse_authors(soup))
    info.update(_parse_rating(soup))
    info.update(_parse_cover(soup))
    return info


def _parse_title(soup) -> dict:
    res = {"status": "", "name": {}}
    title_div = soup.select_one("div.class-title")
    if not title_div:
        return res
    status_texts = [e.get_text(strip=True)
                    for e in title_div.select("div.class-official-group div")]
    res["status"] = " ".join(s for s in status_texts if s).strip()
    short = title_div.select_one("span.short-name")
    if short:
        res["name"]["short-name"] = short.get_text(strip=True)
    cn = title_div.select_one("h3")
    if cn:
        res["name"]["chinese-name"] = cn.get_text(strip=True)
    en = title_div.select_one("h4")
    if en:
        res["name"]["english-name"] = en.get_text(strip=True)
    return res


def _parse_tags(soup) -> dict:
    res = {"tags": []}
    container = soup.select_one("li.col-lg-12.tag")
    if not container:
        return res
    res["tags"] = [a.get_text(strip=True) for a in container.select("ul li a")
                   if a.get_text(strip=True)]
    return res


def _parse_votes(soup) -> dict:
    res = {"votes": {"red_count": "", "red_percentage": "",
                     "black_count": "", "black_percentage": ""}}
    container = soup.select_one("div.text-block")
    if not container:
        return res
    for span in container.select("span"):
        text = span.get_text(strip=True)
        if "红票" in text:
            m = re.search(r"红票(\d+)\s*\((\d+%)\)", text)
            if m:
                res["votes"]["red_count"] = m.group(1)
                res["votes"]["red_percentage"] = m.group(2)
        elif "黑票" in text:
            m = re.search(r"黑票(\d+)\s*\((\d+%)\)", text)
            if m:
                res["votes"]["black_count"] = m.group(1)
                res["votes"]["black_percentage"] = m.group(2)
    return res


def _parse_view_count(soup) -> dict:
    res = {"view_count": ""}
    for el in soup.select("div.span"):
        t = el.select_one("p.t")
        if t and "总浏览" in t.get_text(strip=True):
            n = el.select_one("p.n")
            if n:
                res["view_count"] = n.get_text(strip=True)
            break
    return res


def _parse_mc_versions(soup) -> dict:
    res = {"mc_versions": {}}
    container = soup.select_one("li.col-lg-12.mcver")
    if not container:
        return res
    for ul in container.select("ul"):
        loader_name = None
        versions: list[str] = []
        for li in ul.select("li"):
            text = li.get_text(strip=True)
            if text.endswith(":"):
                potential = text[:-1]
                if not re.match(r"^\d+\.\d+(\.\d+)?$", potential):
                    loader_name = potential
            elif li.select_one("a") and "mcver=" in (li.select_one("a").get("href") or ""):
                v = li.select_one("a").get_text(strip=True)
                if v:
                    versions.append(v)
        if loader_name and versions:
            res["mc_versions"][loader_name] = versions
    return res


def _parse_modpack_count(soup) -> dict:
    res = {"modpack_count": ""}
    container = soup.select_one("li.col-lg-12.infolist.modpack")
    if not container:
        return res
    m = re.search(r"有\s*(\d+)\s*个已收录的整合包使用了", container.get_text(strip=True))
    if m:
        res["modpack_count"] = m.group(1)
    return res


def _parse_authors(soup) -> dict:
    res = {"authors": []}
    container = soup.select_one("li.col-lg-12.author")
    if not container:
        return res
    res["authors"] = [a.get_text(strip=True)
                      for a in container.select("li span.member span.name a")
                      if a.get_text(strip=True)]
    return res


def _parse_rating(soup) -> dict:
    res = {k: 0 for k in _RATING_KEYS}
    rating_block = soup.select_one("div.class-rating-block")
    rating_div = rating_block.find("div", id="class-rating") if rating_block else None
    if not rating_div:
        return res
    title_str = rating_div.get("data-original-title", "")
    if not title_str:
        return res
    import html as _html
    for item in _html.unescape(title_str).split("<br/>"):
        item = item.strip()
        if ":" not in item:
            continue
        key_cn, value_str = item.split(":", 1)
        num = re.search(r"\d+", value_str.strip())
        if num and key_cn.strip() in _CN_TO_EN:
            res[_CN_TO_EN[key_cn.strip()]] = int(num.group())
    return res


def _parse_cover(soup) -> dict:
    res = {"cover": ""}
    cover_div = soup.select_one("div.class-cover-image")
    img = cover_div.find("img") if cover_div else None
    if img:
        src = img.get("src") or ""
        if src.startswith("//"):
            src = f"https:{src}"
        res["cover"] = src
    return res


def _info_text(info: dict, mod_id: str, url: str) -> str:
    """纯文本兜底摘要。"""
    name = info.get("name") or {}
    lines = []
    cn = name.get("chinese-name")
    en = name.get("english-name")
    short = name.get("short-name")
    title = cn or en or f"模组 {mod_id}"
    if short and cn:
        title = f"{short} {cn}"
    lines.append(f"🧩 {title}")
    if info.get("status"):
        lines.append(f"🏷️ 状态: {info['status']}")
    if en and cn:
        lines.append(f"🌐 {en}")
    if info.get("tags"):
        lines.append(f"🏷️ 标签: {'、'.join(info['tags'])}")
    if info.get("view_count"):
        lines.append(f"👁️ 浏览量: {info['view_count']}")
    votes = info.get("votes") or {}
    if votes.get("red_count") or votes.get("black_count"):
        lines.append(f"👍 红票 {votes.get('red_count') or 0} / 👎 黑票 {votes.get('black_count') or 0}"
                     f"（支持 {votes.get('red_percentage') or '0%'}）")
    mv = info.get("mc_versions") or {}
    if mv:
        lines.append("📦 支持版本: " + "；".join(
            f"{k} {v[0]}" + (f"...{v[-1]}" if len(v) > 1 else "") for k, v in mv.items()))
    if info.get("modpack_count"):
        lines.append(f"🎁 {info['modpack_count']} 个整合包使用")
    if info.get("authors"):
        names = info["authors"]
        shown = "、".join(names[:2]) + ("…" if len(names) > 2 else "")
        lines.append(f"👨‍💻 作者: {shown}")
    lines.append(f"🔗 {url}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 卡片渲染（对应 mcmod_card 的 draw_img.py，去掉 base64 改用本地保存）
# --------------------------------------------------------------------------- #
def _make_card(info: dict, cover_bytes: bytes | None, out_path: str) -> None:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    def _font(size: int):
        try:
            return ImageFont.truetype(str(_FONT), size)
        except Exception:
            return ImageFont.load_default()

    font_sm, font_md, font_lg, font_tag = _font(16), _font(20), _font(28), _font(16)

    name = info.get("name") or {}
    short = name.get("short-name", "")
    cn = name.get("chinese-name", "")
    en = name.get("english-name", "")
    status = info.get("status", "")
    tags = info.get("tags") or []
    votes = info.get("votes") or {}
    view_count = info.get("view_count", "")
    mc_versions = info.get("mc_versions") or {}
    authors = info.get("authors") or []
    modpack_count = info.get("modpack_count", "")
    rating = {k: int(info.get(k) or 0) for k in _RATING_KEYS}

    # ---- 画布 ----
    card_w, card_h = 450, 700
    pad, gap = 50, 40
    img_w = pad * 2 + card_w
    img_h = pad * 2 + card_h
    bg = _gradient_background(img_w, img_h, np)
    canvas = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    card_x, card_y = pad, pad
    card_box = (card_x, card_y, card_x + card_w, card_y + card_h)

    # 毛玻璃卡片底
    region = bg.crop(card_box)
    blurred = region.filter(ImageFilter.GaussianBlur(radius=15))
    glass = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 40))
    canvas.paste(Image.alpha_composite(blurred.convert("RGBA"), glass), card_box)
    draw.rounded_rectangle(card_box, radius=20, outline=(255, 255, 255, 200), width=3)

    def _text(pos, content, font, fill=(255, 255, 255), stroke=2):
        draw.text(pos, content, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 140))

    # ---- 封面圆图 ----
    icon = None
    if cover_bytes:
        try:
            icon = Image.open(io.BytesIO(cover_bytes)).convert("RGBA")
            icon = icon.resize((80, 80), Image.Resampling.LANCZOS)
        except Exception:
            icon = None
    if icon is None:
        icon = Image.new("RGBA", (80, 80), (90, 90, 100, 255))
    mask = Image.new("L", (80, 80), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 80, 80), fill=255)
    icon_pos = (card_x + 30, card_y + 30)
    canvas.paste(icon, icon_pos, mask)

    # ---- 名称/状态 ----
    text_x = icon_pos[0] + 80 + 20
    y = card_y + 35
    main = f"{short} {cn}".strip() if cn else (f"{short} {en}".strip() if en else "Mod Name N/A")
    _text((text_x, y), main, font_lg)
    y += font_lg.size + 5
    if en and cn:
        _text((text_x, y), en, font_md, fill=(255, 255, 255))
        y += font_md.size + 5
    if status:
        _text((text_x, y), status, font_sm, fill=(0, 255, 255))
        y += font_sm.size + 5
    y = max(icon_pos[1] + 80 + 10, y + 10)

    # ---- 浏览量 ----
    if view_count:
        _text((icon_pos[0], y), f"浏览量: {view_count}", font_md)
        y += 40
    else:
        y += 40

    # ---- 红黑票 ----
    _draw_votes(draw, icon_pos[0], y, votes, font_sm)
    y += 75

    # ---- 支持版本 ----
    _text((icon_pos[0], y), "支持版本:", font_md)
    y += 28
    for loader, versions in mc_versions.items():
        line = f"{loader}: {versions[0]}" + (f"...{versions[-1]}" if len(versions) > 1 else "")
        _text((icon_pos[0], y), line, font_sm, fill=(255, 255, 0))
        y += 25
    y += 20

    # ---- 标签气泡 ----
    tag_colors = [(100, 180, 255), (255, 165, 0), (162, 155, 254),
                  (255, 107, 107), (129, 236, 236), (255, 234, 167)]
    tag_y = y
    cur_x = icon_pos[0]
    right_bound = card_x + card_w - 20
    if tags:
        for idx, tag in enumerate(tags):
            color = tag_colors[idx % len(tag_colors)]
            bbox = draw.textbbox((0, 0), tag, font=font_tag)
            tw = bbox[2] - bbox[0]
            tag_w = tw + 16
            if cur_x + tag_w > right_bound and cur_x != icon_pos[0]:
                cur_x = icon_pos[0]
                tag_y += 24 + 10
            rect = (cur_x, tag_y, cur_x + tag_w, tag_y + 24)
            draw.rounded_rectangle(rect, radius=8, fill=(*color, 120), outline=(*color, 255), width=1)
            draw.text((cur_x + 8, tag_y + (24 - font_tag.size) // 2 - 1), tag,
                      fill=(255, 255, 255), font=font_tag)
            cur_x += tag_w + 10
        y = tag_y + 24 + 25
    else:
        y += 25

    # ---- 作者 ----
    if authors:
        shown = "、".join(authors[:2]) + ("…" if len(authors) > 2 else "")
        _text((icon_pos[0], y), f"作者: {shown}", font_sm, fill=(255, 120, 120))
        y += 40
    if modpack_count:
        _text((icon_pos[0], y), f"🎁 {modpack_count} 个整合包使用", font_sm)
        y += 40
    y -= 20

    # ---- 八维雷达图 ----
    try:
        values = np.array([rating.get(k, 0) for k in _RATING_KEYS], dtype=float) / 1200.0
        radar = _radar_chart(values, _RATING_CN, (1.0, 0.45, 0.45, 1.0), (220, 220),
                             str(_FONT), FontProperties, plt)
        pos = (card_x + (card_w - radar.width) // 2, y)
        canvas.paste(radar, pos, radar)
    except Exception as e:
        logger.warning(f"[parse] MCMod 雷达图渲染失败: {e}")

    final = Image.alpha_composite(bg.convert("RGBA"), canvas)
    final.save(out_path, format="PNG")
    plt.close("all")


def _gradient_background(width, height, np):
    array = np.zeros((height, width, 3), dtype=np.uint8)
    colors = [np.array([255, 107, 107]), np.array([255, 234, 167]),
              np.array([129, 236, 236]), np.array([162, 155, 254])]
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    distances = [np.sqrt(((x - c[0] * width) ** 2 + (y - c[1] * height) ** 2))
                 for c in [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)]]
    total = sum(1 / (d + 1e-6) for d in distances)
    weights = [(1 / (d + 1e-6)) / total for d in distances]
    for i, color in enumerate(colors):
        array += np.uint8(np.expand_dims(weights[i], axis=-1) * color)
    return Image.fromarray(array)


def _draw_votes(draw, x, y, votes, font_sm):
    try:
        red = int(votes.get("red_count") or 0)
        black = int(votes.get("black_count") or 0)
    except (TypeError, ValueError):
        red, black = 0, 0
    red_pct = votes.get("red_percentage") or "0%"
    black_pct = votes.get("black_percentage") or "0%"
    total = red + black or 1
    bar_w, bar_h = 200, 16
    bar_y = y + 10
    draw.rectangle((x, bar_y, x + bar_w, bar_y + bar_h), outline=(255, 255, 255, 150), width=1)
    red_w = (red / total) * bar_w
    if red_w > 0:
        draw.rectangle((x, bar_y, x + red_w, bar_y + bar_h), fill=(0, 255, 255, 100))
        draw.rectangle((x, bar_y, x + red_w, bar_y + bar_h), outline=(0, 255, 255, 255), width=1)
    black_start = x + red_w
    black_w = (black / total) * bar_w
    if black_w > 0:
        draw.rectangle((black_start, bar_y, black_start + black_w, bar_y + bar_h),
                       fill=(150, 150, 150, 80))
        draw.rectangle((black_start, bar_y, black_start + black_w, bar_y + bar_h),
                       outline=(150, 150, 150, 255), width=1)
    if red_w > 30:
        draw.text((x + red_w / 2, bar_y + bar_h / 2), red_pct, fill=(255, 255, 255),
                  font=font_sm, anchor="mm")
    if black_w > 30:
        draw.text((black_start + black_w / 2, bar_y + bar_h / 2), black_pct,
                  fill=(255, 255, 255), font=font_sm, anchor="mm")
    draw.text((x, bar_y + bar_h + 5), f"支持: {red} / 反对: {black}",
              fill=(220, 220, 220), font=font_sm)


def _radar_chart(values, labels, color, size, font_path, FontProperties, plt):
    import numpy as np
    data = np.concatenate((values, [values[0]]))
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]
    fig, ax = plt.subplots(figsize=(size[0] / 100, size[1] / 100),
                           subplot_kw=dict(polar=True))
    ax.set_facecolor((0, 0, 0, 0))
    fig.patch.set_alpha(0.0)
    ax.plot(angles, data, color=color, linewidth=2, zorder=3)
    ax.fill(angles, data, color=color, alpha=0.3, zorder=2)
    try:
        fp = FontProperties(fname=font_path, size=12)
    except Exception:
        fp = None
    ax.set_yticklabels([])
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color="white", fontproperties=fp, y=-0.1)
    ax.spines["polar"].set_visible(False)
    ax.grid(color=(1, 1, 1, 0.4), linestyle="--", linewidth=0.5, zorder=1)
    ax.set_rlim(0, 1)
    ax.set_yticks(np.arange(0.2, 1.2, 0.2))
    buf = io.BytesIO()
    plt.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0.1)
    buf.seek(0)
    plt.close(fig)
    return Image.open(buf).convert("RGBA")
