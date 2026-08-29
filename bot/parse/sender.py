# -*- coding: utf-8 -*-
"""解析结果发送器（本 bot 版本）：把 ParseResult 转成本 bot 可发的文本/图片/视频/文件。"""
import os
import re
import shutil
import tempfile
from itertools import chain
from pathlib import Path
from ._log import logger
from .config import PluginConfig
from .data import (
    AudioContent,
    DynamicContent,
    FileContent,
    GraphicsContent,
    ImageContent,
    TextContent,
    ParseResult,
    SendGroup,
    VideoContent,
)
from .exception import (
    DownloadException,
    DownloadLimitException,
    DurationLimitException,
    SizeLimitException,
    ZeroSizeException,
)
from .render import Renderer

# file_type：image=1 video=2 audio=3 file=4
FT_IMAGE = 1
FT_VIDEO = 2
FT_AUDIO = 3
FT_FILE = 4

# 视频超过该值不发文件（和原 B站解析 AUTO_VIDEO_MB 一致，避免被降级成群文件）
_VIDEO_MAX_MB = 30


class MessageSender:
    def __init__(self, config: PluginConfig, renderer: Renderer):
        self.cfg = config
        self.renderer = renderer
        self._cleanup_paths: list[Path] = []  # 发送用的可读文件名副本，发送后清理
        self._source_paths: list[Path] = []  # 本次解析下载的重媒体源文件（发完即删，不留缓存）

    def _pretty_media(self, path: Path, title: str | None, artist: str | None = None) -> Path:
        """给音频/文件生成「标题_歌手」命名的临时副本，作为发送文件名。

        原始下载文件是 uuid 乱码名，QQ 文件卡片会显示这个名字；这里用标题+歌手重建一个
        可读文件名副本去发送（原下载文件不动）。
        """
        if not title:
            return path
        base = title if not artist else f"{title}_{artist}"
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", base).strip().strip(".") or "media"
        ext = path.suffix.lower() or ".mp3"
        tmp = Path(tempfile.gettempdir()) / "qqbot_media"
        tmp.mkdir(parents=True, exist_ok=True)
        new = tmp / f"{safe[:60]}{ext}"
        n = 1
        while new.exists():
            new = tmp / f"{safe[:60]}_{n}{ext}"
            n += 1
        try:
            shutil.copyfile(path, new)
        except Exception:
            return path
        self._cleanup_paths.append(new)
        return new

    def _cleanup(self):
        # 重媒体源文件发完即删（不可复用缓存）
        for p in list(self._source_paths):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self._source_paths.clear()
        for p in list(self._cleanup_paths):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        self._cleanup_paths.clear()

    @staticmethod
    def _iter_contents(result: ParseResult):
        return chain(result.contents, result.repost.contents if result.repost else ())

    def _build_plan(self, result, contents):
        light, heavy = [], []
        for cont in contents:
            if isinstance(cont, (ImageContent, GraphicsContent, TextContent)):
                light.append(cont)
            else:
                heavy.append(cont)
        is_single_heavy = len(heavy) == 1 and not light
        render_card = is_single_heavy and bool(self.cfg.single_heavy_render_card)
        seg_count = len(light) + len(heavy) + (1 if render_card else 0)
        force_merge = seg_count >= int(self.cfg.forward_threshold or 99)
        return {"light": light, "heavy": heavy, "render_card": render_card,
                "preview_card": render_card and not force_merge}

    async def _preview_card(self, result) -> Path | None:
        if not hasattr(self, "_last_plan") or not self._last_plan["preview_card"]:
            return None
        try:
            return await self.renderer.render_card(result)
        except Exception as e:
            logger.warning(f"[parse] 预览卡片渲染失败: {e}")
            return None

    async def _build_segments(self, result, plan):
        segs: list[tuple] = []  # (kind, path|text, name|None)
        if plan["render_card"]:
            try:
                if p := await self.renderer.render_card(result):
                    segs.append(("image", p, None))
            except Exception as e:
                logger.warning(f"[parse] 卡片渲染失败: {e}")

        for cont in plan["light"]:
            if isinstance(cont, TextContent):
                if cont.text:
                    segs.append(("text", cont.text, None))
                continue
            try:
                path = await cont.get_path()
            except (DownloadLimitException, ZeroSizeException):
                continue
            except DownloadException:
                if self.cfg.show_download_fail_tip:
                    segs.append(("text", "此项媒体下载失败", None))
                continue
            if isinstance(cont, GraphicsContent):
                segs.append(("image", path, None))
                if cont.text:
                    segs.append(("text", cont.text, None))
                if cont.alt:
                    segs.append(("text", cont.alt, None))
            else:
                segs.append(("image", path, None))

        for cont in plan["heavy"]:
            try:
                path = await cont.get_path()
            except (DurationLimitException,) as exc:
                if self.cfg.show_download_fail_tip:
                    segs.append(("text", "此项媒体超过时长限制", None))
                continue
            except (SizeLimitException,) as exc:
                if self.cfg.show_download_fail_tip:
                    segs.append(("text", "此项媒体超过大小限制", None))
                continue
            except DownloadException:
                if self.cfg.show_download_fail_tip:
                    segs.append(("text", "此项媒体下载失败", None))
                continue
            if isinstance(cont, (VideoContent, DynamicContent)):
                self._source_paths.append(path)  # 视频发完即删
                segs.append(("video", path, None))
            elif isinstance(cont, AudioContent):
                artist = result.author.name if result.author else None
                self._source_paths.append(path)  # 音频源文件发完即删
                segs.append(("audio", self._pretty_media(path, result.title, artist), path.name))
            elif isinstance(cont, FileContent):
                artist = result.author.name if result.author else None
                self._source_paths.append(path)
                segs.append(("file", self._pretty_media(path, result.title, artist), path.name))
        return segs

    @staticmethod
    def _fallback_text(result) -> str:
        lines = []
        if result.header:
            lines.append(result.header)
        if result.text:
            lines.append(result.text)
        elif result.extra.get("info"):
            lines.append(str(result.extra["info"]))
        return "\n".join(l for l in lines if l).strip()

    async def send_parse_result(self, ctx, sender, result) -> bool:
        """把 ParseResult 发回当前会话。ctx 是我 bot 的命令上下文；sender 是 Sender 实例。"""
        groups = result.send_groups or [SendGroup(contents=list(self._iter_contents(result)))]
        sent_any = False
        try:
            for group in groups:
                plan = self._build_plan(result, group.contents)
                self._last_plan = plan
                segs = await self._build_segments(result, plan)
                if not segs:
                    continue
                msg = getattr(ctx, "message", None)
                for kind, payload, name in segs:
                    try:
                        if kind == "text":
                            await sender.send_text(msg, payload, reply=False)
                        elif kind == "image":
                            await sender.send_local_file(msg, FT_IMAGE, payload, reply=False)
                        elif kind == "video":
                            # 超 30MB 不发视频（和原 B站解析一致），提示吃撑
                            sz_mb = os.path.getsize(payload) / 1024 / 1024
                            if sz_mb > _VIDEO_MAX_MB:
                                await sender.send_text(
                                    msg,
                                    f"吃撑了喵（{result.title or '视频'}实际 {sz_mb:.1f}MB，超过 {_VIDEO_MAX_MB:g}MB），睡大觉了喵",
                                    reply=False,
                                )
                            else:
                                await sender.send_local_file(msg, FT_VIDEO, payload, reply=False)
                        elif kind == "audio":
                            # 音乐用「语音」发送（file_type=3）：实测官方 webhook 语音不限长，
                            # 整首歌（4分22秒已验证）可整体发出，客户端可直接播放。
                            await sender.send_local_file(msg, FT_AUDIO, payload, reply=False)
                        elif kind == "file":
                            await sender.send_local_file(msg, FT_FILE, payload, reply=False)
                        sent_any = True
                    except Exception as e:
                        logger.warning(f"[parse] 发送段失败 {kind}: {e}")
            if not sent_any:
                text = self._fallback_text(result)
                if text:
                    await sender.send_text(getattr(ctx, "message", None), text, reply=False)
                    sent_any = True
        finally:
            self._cleanup()
        return sent_any