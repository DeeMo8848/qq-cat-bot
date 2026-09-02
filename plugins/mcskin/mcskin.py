# -*- coding: utf-8 -*-
"""Minecraft 皮肤渲染图查询。

「皮肤 <玩家名>」默认走在线渲染 API（mc-heads 主 / crafatar 备）出 3D 全身图，
效果同 Minecraft 皮肤站；在线失败时自动回落本地等距渲染。
「下载皮肤 <玩家名>」直接发该玩家 Mojang 原版 64x64 皮肤贴图。
还支持引用/发送一张 64x64 皮肤贴图再发「皮肤」，用本地渲染出图（自定义皮无上传地址）。
"""

import asyncio
import io
import os
import uuid as _uuid

import aiohttp
from PIL import Image

from config import ROOT
from bot.commands import register, ROLE_ALL

# 供 Web 后台「其他功能 → MC皮肤」插件总开关使用的命令名集合
MCSKIN_CMD_NAMES = {"cmd_mcskin", "cmd_dlskin"}

_MOJANG_API = "https://api.mojang.com/users/profiles/minecraft/{username}"
_ASHC0N_API = "https://api.ashcon.app/mojang/v2/user/{username}"
_PROFILE_API = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"

_DEFAULT_RENDERTYPE = "body"
_ALIAS = {"head": "head", "头像": "head", "body": "body", "全身": "body"}

_TIMEOUT = aiohttp.ClientTimeout(total=25)
_BROWSER_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
}


def _matcher(t):
    """宽松匹配：以「皮肤」开头的消息触发（需要跟玩家名参数或引用图片）。"""
    t = (t or "").strip().lower()
    return t.startswith("皮肤") and len(t) > 2


async def _uuid_of(username):
    """把玩家名解析为 UUID：先 Mojang，失败走 ashcon 镜像。"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_MOJANG_API.format(username=username),
                             timeout=_TIMEOUT, ssl=False) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    uid = (data or {}).get("id")
                    if uid:
                        return uid, None
                elif r.status == 404:
                    return None, f"找不到玩家 '{username}' 喵"
    except Exception:
        pass
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_ASHC0N_API.format(username=username),
                             timeout=_TIMEOUT, ssl=False) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    uid = (data or {}).get("uuid") or (data or {}).get("id")
                    if uid:
                        return uid, None
    except Exception:
        pass
    return None, f"获取玩家 '{username}' 信息失败喵（解析不到 UUID）"


async def _texture_bytes_of(uuid):
    """取 Mojang 账号的真实皮肤贴图字节；无自定义皮肤或失败返回 None。"""
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.get(_PROFILE_API.format(uuid=uuid),
                             timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        import base64
        import json
        tex_url = None
        for p in (data or {}).get("properties", []) or []:
            if p.get("name") != "textures":
                continue
            try:
                payload = json.loads(base64.b64decode(p["value"]))
                tex_url = (payload.get("textures") or {}).get("SKIN", {}).get("url")
            except Exception:
                tex_url = None
            break
        if not tex_url:
            return None
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s2:
            async with s2.get(tex_url, timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


async def _fetch_bytes(url):
    try:
        async with aiohttp.ClientSession(headers=_BROWSER_HDRS) as s:
            async with s.get(url, timeout=_TIMEOUT, ssl=False) as r:
                if r.status != 200:
                    return None
                return await r.read()
    except Exception:
        return None


# ---------- 在线渲染（mc-heads 主 / crafatar 备）----------
_RENDER_URLS = {
    # rendertype: [(主, 备)]，按序探测
    "body": [
        "https://mc-heads.net/body/{uuid}/512",
        "https://crafatar.com/renders/body/{uuid}?size=512&overlay=true",
    ],
    "head": [
        "https://mc-heads.net/head/{uuid}/512",
        "https://crafatar.com/avatars/{uuid}?size=512&overlay=true",
    ],
}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _is_png(data):
    return bool(data) and data.startswith(_PNG_MAGIC)


async def _render_online(uuid, rendertype="body"):
    """用在线 API 渲染玩家皮肤，成功返回本地临时 png 路径，失败返回 None。"""
    for url in _RENDER_URLS.get(rendertype, _RENDER_URLS["body"]):
        try:
            data = await _fetch_bytes(url.format(uuid=uuid))
        except Exception:
            data = None
        if data and _is_png(data):
            os.makedirs(os.path.join(ROOT, "tmp", "mcskin"), exist_ok=True)
            out = os.path.join(ROOT, "tmp", "mcskin", _uuid.uuid4().hex + ".png")
            try:
                with open(out, "wb") as f:
                    f.write(data)
                return out
            except Exception:
                try:
                    os.remove(out)
                except Exception:
                    pass
    return None


# ---------- 本地皮肤渲染 ----------
_SKIN_OK_SIZES = {(64, 64), (64, 32)}


def _check_texture(texture_bytes):
    """校验是否为有效的皮肤贴图；返回 (ok, 错误提示)。"""
    try:
        img = Image.open(io.BytesIO(texture_bytes))
        img.load()
    except Exception:
        return False, "这不像一张有效的图片喵，请上传 64x64 的皮肤贴图"
    if img.mode not in ("RGBA", "RGB", "P", "L"):
        img = img.convert("RGBA")
    if (img.width, img.height) not in _SKIN_OK_SIZES:
        return False, (f"皮肤贴图尺寸应为 64x64（或 64x32），你的是 "
                       f"{img.width}x{img.height}，无法渲染喵")
    return True, ""


def _render_sync(texture_bytes, out_path, scale=16):
    """把 64x64 皮肤贴图渲染成一张等距 3D 站姿 PNG。

    每个身体部位按「顶面 + 正面 + 右侧面」三个可见面做斜切投影，
    面朝向/贴图 UV 对齐 Minecraft 标准 64x64 肤贴布局。
    """
    tex = Image.open(io.BytesIO(texture_bytes)).convert("RGBA")
    if tex.size == (64, 32):
        # 经典 legacy 皮肤只有上半区：下半区=上半区垂直镜像，补齐成 64x64
        tex64 = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        tex64.paste(tex, (0, 0))
        tex64.paste(tex.transpose(Image.FLIP_TOP_BOTTOM), (0, 32))
        tex = tex64

    def _slim():
        try:
            return tex.getpixel((54, 20))[3] < 8
        except Exception:
            return False
    slim = _slim()

    u = scale          # 每个方块在水平面的横向像素
    v = max(1, u // 4) # 斜切带来的竖直偏移（浅俯仰角，避免身体顶面砍掉四肢肩部）
    ht = u             # 每个方块的竖直像素
    OX = scale * 14    # 投影基准偏移，保证画面落在画布正中间
    OY = 33 * scale - 4 * v   # 依 v 自适应下移，头冠不会被裁出画布顶部

    def proj(x, z, y):
        # x: 角色横向, z: 朝向镜头/纵深, y: 高度
        return (x - z) * u + OX, (x + z) * v - y * ht + OY

    canvas = Image.new("RGBA", (scale * 64, scale * 96), (0, 0, 0, 0))

    def crop(uv):
        tx, ty, tw, th = uv
        return tex.crop((tx, ty, tx + tw, ty + th))

    def _over(canvas, nx, ny, col):
        # 手写源上(source-over)合成
        r, g, b, a = col
        r0, g0, b0, a0 = canvas.getpixel((nx, ny))
        if a == 255:
            canvas.putpixel((nx, ny), col)
        elif a0 == 0:
            canvas.putpixel((nx, ny), col)
        else:
            aoch = a / 255.0 + (a0 / 255.0) * (1 - a / 255.0)
            if aoch <= 0:
                return
            rr = (r * (a / 255.0) + r0 * (a0 / 255.0) * (1 - a / 255.0)) / aoch
            gg = (g * (a / 255.0) + g0 * (a0 / 255.0) * (1 - a / 255.0)) / aoch
            bb = (b * (a / 255.0) + b0 * (a0 / 255.0) * (1 - a / 255.0)) / aoch
            canvas.putpixel((nx, ny), (int(rr), int(gg), int(bb), int(aoch * 255)))

    def draw_face(uv, pts):
        """把贴图一块(w×h)仿射拉伸到 pts=[TL,TR,BR,BL] 组成的平行四边形里。"""
        import math
        face = crop(uv)
        sw, sh = face.size
        TL, TR, _BR, BL = [tuple(float(v) for v in p) for p in pts]
        ux, uy = TR[0] - TL[0], TR[1] - TL[1]
        vx, vy = BL[0] - TL[0], BL[1] - TL[1]
        det = ux * vy - uy * vx
        if abs(det) < 1e-6:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = int(math.floor(min(xs))), int(math.ceil(max(xs)))
        y0, y1 = int(math.floor(min(ys))), int(math.ceil(max(ys)))
        fdat = face.load()
        W, H = canvas.width, canvas.height
        for py in range(y0, y1 + 1):
            for px in range(x0, x1 + 1):
                dx, dy = px - TL[0], py - TL[1]
                u = (dx * vy - dy * vx) / det
                v = (dy * ux - dx * uy) / det
                if u < 0.0 or v < 0.0 or u > 1.0 or v > 1.0:
                    continue
                sx = min(sw - 1, int(u * sw))
                sy = min(sh - 1, int(v * sh))
                col = fdat[sx, sy]
                if col[3] == 0:
                    continue
                if 0 <= px < W and 0 <= py < H:
                    _over(canvas, px, py, col)

    def draw_box(x0, x1, z0, z1, y0, y1, uv, overlay_uv=None):
        # 可见面：顶面、正面、右侧面
        pts_top = [proj(x0, z0, y1), proj(x1, z0, y1), proj(x1, z1, y1), proj(x0, z1, y1)]
        pts_front = [proj(x0, z0, y1), proj(x1, z0, y1), proj(x1, z0, y0), proj(x0, z0, y0)]
        pts_right = [proj(x1, z0, y1), proj(x1, z1, y1), proj(x1, z1, y0), proj(x1, z0, y0)]
        draw_face(uv["top"], pts_top)
        draw_face(uv["right"], pts_right)
        draw_face(uv["front"], pts_front)
        if overlay_uv:
            draw_face(overlay_uv["top"], pts_top)
            draw_face(overlay_uv["right"], pts_right)
            draw_face(overlay_uv["front"], pts_front)

    aw = 3 if slim else 4  # 手臂宽（slim/alex）

    # 部件坐标（x 总宽 16，中心对称，z 纵深、y 高度）
    rx = 16 - aw                # 右臂左边界
    body_uv = {"top": (20, 16, 8, 4), "front": (20, 20, 8, 12), "right": (16, 20, 4, 12)}
    head_uv = {"top": (8, 0, 8, 8), "front": (8, 8, 8, 8), "right": (0, 8, 8, 8)}
    hat_uv = {"top": (40, 0, 8, 8), "front": (40, 8, 8, 8), "right": (32, 8, 8, 8)}
    rarm_uv = {"top": (44, 16, 4, 4), "front": (44, 20, 4, 12), "right": (40, 20, 4, 12)}
    larm_uv = {"top": (32, 48, 4, 4), "front": (36, 52, 4, 12), "right": (32, 52, 4, 12)} if not slim \
        else {"top": (32, 48, 3, 4), "front": (36, 52, 3, 12), "right": (32, 52, 3, 12)}
    rleg_uv = {"top": (20, 48, 4, 4), "front": (20, 52, 4, 12), "right": (16, 52, 4, 12)}
    lleg_uv = {"top": (4, 16, 4, 4), "front": (4, 20, 4, 12), "right": (0, 20, 4, 12)}

    # 绘制：稍远先画（右臂/右腿先，身体与头最后，避免遮挡）
    draw_box(rx, rx + aw, 3, 7, 12, 24, rarm_uv)            # 画面右臂（角色右手）
    draw_box(0, aw, 3, 7, 12, 24, larm_uv)                  # 画面左臂（角色左手）
    draw_box(8, 12, 3, 7, 0, 12, rleg_uv)                   # 画面右腿（角色右腿）
    draw_box(4, 8, 3, 7, 0, 12, lleg_uv)                    # 画面左腿（角色左腿）
    draw_box(4, 12, 3, 7, 12, 24, body_uv)                  # 身体（后画，盖住臂根）
    draw_box(4, 12, 0, 8, 24, 32, head_uv, overlay_uv=hat_uv)  # 头+帽子

    # 裁掉透明留白，四周留一点边距，避免肢体贴到画布边缘
    bbox = canvas.getbbox()
    if bbox:
        pad = u + 10
        canvas = canvas.crop((
            max(0, bbox[0] - pad), max(0, bbox[1] - pad),
            min(canvas.width, bbox[2] + pad), min(canvas.height, bbox[3] + pad),
        ))
    canvas.save(out_path, format="PNG")
    return out_path


def _arm_half(uv):
    return uv


async def _render_texture(texture_bytes):
    loop = asyncio.get_running_loop()
    os.makedirs(os.path.join(ROOT, "tmp", "mcskin"), exist_ok=True)
    out = os.path.join(ROOT, "tmp", "mcskin", _uuid.uuid4().hex + ".png")
    await loop.run_in_executor(None, _render_sync, texture_bytes, out)
    return out


def _usage():
    return (
        "皮肤用法喵：\n"
        "· 皮肤 <玩家名> → 渲染该玩家的全身皮肤(在线 3D 图)\n"
        "· 皮肤 head <玩家名> / 皮肤 头像 <玩家名> → 只渲染头像\n"
        "· 引用/发送一张 64x64 皮肤贴图再发 皮肤 → 渲染你的自定义皮肤\n"
        "· 下载皮肤 <玩家名> → 直接发该玩家原版 64x64 皮肤贴图文件"
    )


async def _send_local(path, ctx):
    try:
        await ctx.sender.send_local_file(ctx.message, 1, path, reply=False)
        return True
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        return False


@register(keywords=["皮肤"], help="渲染MC皮肤（皮肤 <玩家名>；或引用贴图+皮肤）喵", matcher=_matcher, role=ROLE_ALL)
async def cmd_mcskin(ctx):
    import re as _re
    rest = _re.sub(r"^皮肤\s*", "", (getattr(ctx, "args", "") or "").strip()).strip()

    # 优先：消息带图片（引用/直发）→ 视为自定义皮肤贴图
    image_urls = getattr(ctx.message, "image_urls", None) or []
    if not rest or image_urls:
        if image_urls:
            tex = await _fetch_bytes(image_urls[0])
            if not tex:
                await ctx.reply("皮肤贴图下载失败，请重试喵")
                return
            ok, err = await asyncio.get_running_loop().run_in_executor(
                None, _check_texture, tex)
            if not ok:
                await ctx.reply(err)
                return
            await ctx.reply_text("🎨 收到皮肤贴图，正在给主人渲染喵~")
            out = await _render_texture(tex)   # 自定义贴图没有可上传地址，走本地渲染
            if not await _send_local(out, ctx):
                await ctx.reply("皮肤图片发送失败喵")
            try:
                os.remove(out)
            except Exception:
                pass
            return

    # 识别 渲染类型 + 玩家名
    parts = rest.split()
    rendertype = _DEFAULT_RENDERTYPE
    if len(parts) >= 2 and parts[0].lower() in _ALIAS:
        rendertype, username = _ALIAS[parts[0].lower()], parts[1]
    else:
        username = parts[0] if parts else ""
    if not username:
        await ctx.reply(_usage())
        return
    await ctx.reply_text("🎨 正在给主人渲染皮肤喵，稍等片刻~")

    uid, err = await _uuid_of(username)
    if err:
        await ctx.reply(err)
        return

    # 先走在线渲染 API；失败再回落本地渲染
    out = await _render_online(uid, rendertype)
    if out and await _send_local(out, ctx):
        return
    try:
        if out:
            os.remove(out)
    except Exception:
        pass

    tex = await _texture_bytes_of(uid)
    if not tex:
        await ctx.reply("该玩家似乎没有可渲染的皮肤，或皮肤服务暂时不可用喵")
        return
    ok, err = await asyncio.get_running_loop().run_in_executor(
        None, _check_texture, tex)
    if not ok:
        await ctx.reply(err)
        return
    out = await _render_texture(tex)
    if not await _send_local(out, ctx):
        await ctx.reply("皮肤图片发送失败喵")
    try:
        os.remove(out)
    except Exception:
        pass


def _matcher_dl(t):
    """「下载皮肤」精确前缀触发（需要跟玩家名）。"""
    t = (t or "").strip().lower()
    return t.startswith("下载皮肤") and len(t) > 4


@register(keywords=["下载皮肤"], help="下载皮肤 <玩家名>：获取该玩家的原版皮肤贴图文件喵", matcher=_matcher_dl, role=ROLE_ALL)
async def cmd_dlskin(ctx):
    import re as _re
    rest = _re.sub(r"^下载皮肤\s*", "", (getattr(ctx, "args", "") or "").strip()).strip()
    username = rest.split()[0] if rest.split() else ""
    if not username:
        await ctx.reply("用法喵：下载皮肤 <玩家名>，例如「下载皮肤 Notch」")
        return
    await ctx.reply_text("📥 正在为你下载皮肤贴图喵，稍等~")

    uid, err = await _uuid_of(username)
    if err:
        await ctx.reply(err)
        return
    tex = await _texture_bytes_of(uid)
    if not tex:
        await ctx.reply("没拿到该玩家的皮肤贴图喵，或皮肤服务暂时不可用")
        return
    ok, emsg = await asyncio.get_running_loop().run_in_executor(None, _check_texture, tex)
    if not ok:
        await ctx.reply(emsg)
        return
    # 原版贴图本身就是一张 PNG 图片，直接发图，长按可保存
    os.makedirs(os.path.join(ROOT, "tmp", "mcskin"), exist_ok=True)
    out = os.path.join(ROOT, "tmp", "mcskin", _uuid.uuid4().hex + ".png")
    try:
        with open(out, "wb") as f:
            f.write(tex)
    except Exception:
        await ctx.reply("皮肤贴图保存失败喵")
        return
    try:
        await ctx.sender.send_local_file(ctx.message, 1, out, reply=False)
        await ctx.reply(f"✅ 已把「{username}」的原版皮肤贴图(64x64)发给你喵，长按可保存~")
    except Exception:
        await ctx.reply("皮肤贴图发送失败喵")
    finally:
        try:
            os.remove(out)
        except Exception:
            pass