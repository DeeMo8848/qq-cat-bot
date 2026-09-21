from asyncio import Task, TimeoutError, create_task, gather, sleep, to_thread
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import aiofiles
import yt_dlp
from aiohttp import ClientError, ClientSession, ClientTimeout
from msgspec import Struct, convert
from tqdm.asyncio import tqdm
from ._log import logger

from .config import PluginConfig
from .constants import COMMON_HEADER
from .exception import (
    DownloadException,
    DurationLimitException,
    ParseException,
    SizeLimitException,
    ZeroSizeException,
)
from .utils import (
    LimitedSizeDict,
    find_ffmpeg,
    generate_file_name,
    merge_av,
    safe_unlink,
)

P = ParamSpec("P")
T = TypeVar("T")


def _ffmpeg_location() -> str | None:
    """返回 ffmpeg 所在目录（供 yt-dlp 定位），内置路径无效时用 PATH 则返回 None。"""
    exe = Path(find_ffmpeg())
    if exe.is_file():
        return str(exe.parent)
    return None


def auto_task(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Task[T]]:
    """装饰器：自动将异步函数调用转换为 Task, 完整保留类型提示"""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Task[T]:
        coro = func(*args, **kwargs)
        name = " | ".join(str(arg) for arg in args if isinstance(arg, str))
        return create_task(coro, name=func.__name__ + " | " + name)

    return wrapper


# Content-Type → 扩展名。QQ 富媒体上传靠扩展名判格式，无后缀的音频会被判「格式不支持」。
_CTYPE_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _sniff_ext(head: bytes) -> str | None:
    """从文件头字节猜扩展名（Content-Type 缺失/不可信时兜底）。"""
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:2] == b"\xff\xf1" or head[:2] == b"\xff\xf9":
        return ".aac"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


class VideoInfo(Struct):
    title: str
    """标题"""
    channel: str
    """频道名称"""
    uploader: str
    """上传者 id"""
    duration: int
    """时长"""
    timestamp: int
    """发布时间戳"""
    thumbnail: str
    """封面图片"""
    description: str
    """简介"""
    channel_id: str
    """频道 id"""

    @property
    def author_name(self) -> str:
        return f"{self.channel}@{self.uploader}"


class Downloader:
    """下载器，支持youtube-dlp 和 流式下载"""

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.max_size = self.cfg.source_max_size
        self.default_headers: dict[str, str] = COMMON_HEADER.copy()
        # 视频信息缓存
        self.info_cache: LimitedSizeDict[str, VideoInfo] = LimitedSizeDict()
        # 用于流式下载的客户端
        self.client = ClientSession(
            timeout=ClientTimeout(total=self.cfg.download_timeout)
        )

    async def close(self):
        """关闭网络客户端"""
        await self.client.close()

    @staticmethod
    async def _ensure_media_suffix(
        file_path: Path, response, head_bytes: bytes | None = None
    ) -> Path:
        """下载落盘后，若文件没有可用扩展名则按 Content-Type / 文件头补一个。

        背景：网易云音频直链形如 `http://m804.music.126.net/<时间戳>/<hash>/xxx`，
        URL path 没有 `.mp3`，`generate_file_name` 只能生成 `d41d8cd98f00b204`（无后缀）。
        QQ 富媒体上传按扩展名判格式，无后缀的音频会被服务端拒绝。
        只处理「没有后缀」的情况；已有后缀的原样返回，不产生多余 IO。
        """
        if file_path.suffix:
            return file_path
        try:
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            # Content-Type 常见 text/plain / application/octet-stream，不可信 → 优先嗅探文件头
            ext = _sniff_ext(head_bytes or b"")
            if not ext:
                ext = _CTYPE_EXT.get(ctype)
            if not ext:
                return file_path
            new_path = file_path.with_suffix(ext)
            if new_path.exists():
                await safe_unlink(new_path)
            file_path.rename(new_path)
            logger.info(f"媒体无后缀，按文件头/Content-Type 补正为 {new_path.name}")
            return new_path
        except Exception as e:  # 补后缀失败不影响主流程
            logger.warning(f"补正媒体后缀失败（保持原样）: {e}")
            return file_path

    @auto_task
    async def streamd(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        """流式下载"""
        if not file_name:
            file_name = generate_file_name(url)
        file_path = self.cfg.cache_dir / file_name
        # 如果文件存在，则直接返回
        if file_path.exists():
            return file_path
        headers = headers or self.default_headers
        retries = self.cfg.download_retry_times
        for attempt in range(retries + 1):
            try:
                async with self.client.get(
                    url, headers=headers, allow_redirects=True, proxy=proxy
                ) as response:
                    if response.status >= 400:
                        raise ClientError(f"HTTP {response.status} {response.reason}")
                    content_length = response.content_length
                    max_bytes = self.max_size * 1024 * 1024

                    if content_length == 0:
                        logger.warning(f"媒体 url: {url}, 大小为 0, 取消下载")
                        raise ZeroSizeException
                    if content_length and content_length > max_bytes:
                        logger.warning(
                            f"媒体 url: {url} 大小 {content_length / 1024 / 1024:.2f} MB 超过 {self.max_size} MB, 取消下载"
                        )
                        raise SizeLimitException

                    downloaded = 0
                    with self.get_progress_bar(file_name, content_length) as bar:
                        async with aiofiles.open(file_path, "wb") as file:
                            async for chunk in response.content.iter_chunked(
                                1024 * 1024
                            ):
                                downloaded += len(chunk)
                                if downloaded > max_bytes:
                                    raise SizeLimitException
                                await file.write(chunk)
                                bar.update(len(chunk))

                    if downloaded == 0:
                        logger.warning(f"媒体 url: {url}, 实际大小为 0, 取消下载")
                        raise ZeroSizeException
                    if content_length and downloaded < content_length:
                        raise ClientError(
                            f"HTTP payload incomplete {downloaded}/{content_length}"
                        )

                    # ★ 内容校验：下载到的可能是错误页而不是媒体。
                    #   实测网易云 outer 兜底链对无版权/需 VIP 的曲子会回 200 +
                    #   一个 100KB 左右的 HTML（`<!DOCTYPE html>`），若直接当歌曲发出去，
                    #   群里收到的是一个「打不开的音频」。这里按文件头识别并拒掉。
                    with open(file_path, "rb") as f:
                        head_bytes = f.read(512)
                    low_head = head_bytes[:512].lstrip().lower()
                    if (low_head.startswith(b"<!doctype") or low_head.startswith(b"<html")
                            or low_head.startswith(b"{\"") or low_head.startswith(b"<?xml")):
                        logger.warning(f"媒体 url: {url} 下载到的是网页/JSON 而非媒体，丢弃")
                        raise ZeroSizeException

                    # 无后缀则按 Content-Type / 文件头补正（QQ 上传要按扩展名判格式）
                    file_path = await self._ensure_media_suffix(file_path, response, head_bytes)
                    return file_path
            except (ZeroSizeException, SizeLimitException):
                await safe_unlink(file_path)
                raise
            except (ClientError, TimeoutError) as exc:
                await safe_unlink(file_path)
                if attempt < retries:
                    await sleep(1 + attempt)
                    continue
                logger.exception(f"下载失败 | url: {url}, file_path: {file_path}")
                raise DownloadException("媒体下载失败") from exc
        raise DownloadException("媒体下载失败")

    @staticmethod
    def get_progress_bar(desc: str, total: int | None = None) -> tqdm:
        """获取进度条 bar

        Args:
            desc (str): 描述
            total (int | None): 总大小. Defaults to None.

        Returns:
            tqdm: 进度条
        """
        return tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            colour="green",
            desc=desc,
        )

    @auto_task
    async def download_video(
        self,
        url: str,
        *,
        video_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if video_name is None:
            video_name = generate_file_name(url, ".mp4")
        return await self.streamd(
            url, file_name=video_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_audio(
        self,
        url: str,
        *,
        audio_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        if audio_name is None:
            audio_name = generate_file_name(url, ".mp3")
        return await self.streamd(
            url, file_name=audio_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_file(
        self,
        url: str,
        *,
        file_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        if file_name is None:
            file_name = generate_file_name(url, ".zip")
        return await self.streamd(
            url, file_name=file_name, headers=headers, proxy=proxy
        )

    @auto_task
    async def download_img(
        self,
        url: str,
        *,
        img_name: str | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> Path:
        if img_name is None:
            img_name = generate_file_name(url, ".jpg")
        return await self.streamd(url, file_name=img_name, headers=headers, proxy=proxy)

    async def download_imgs_without_raise(
        self,
        urls: list[str],
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None | object = ...,
    ) -> list[Path]:
        paths_or_errs = await gather(
            *[self.download_img(url, headers=headers, proxy=proxy) for url in urls],
            return_exceptions=True,
        )
        return [p for p in paths_or_errs if isinstance(p, Path)]

    @auto_task
    async def download_av_and_merge(
        self,
        v_url: str,
        a_url: str,
        *,
        output_path: Path,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ) -> Path:
        """
        download video and audio file by url with stream and merge
        """
        v_path, a_path = await gather(
            self.download_video(v_url, headers=headers, proxy=proxy),
            self.download_audio(a_url, headers=headers, proxy=proxy),
        )
        await merge_av(v_path=v_path, a_path=a_path, output_path=output_path)
        return output_path

    async def ytdlp_extract_info(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> VideoInfo:
        if (info := self.info_cache.get(url)) is not None:
            return info
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not raw:
                raise ParseException("获取视频信息失败")
        info = convert(raw, VideoInfo)
        self.info_cache[url] = info
        return info

    async def ytdlp_extract_raw(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> dict[str, Any]:
        opts = {
            "quiet": True,
            "skip_download": True,
            "http_headers": headers or self.default_headers,
        }
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if format:
            opts["format"] = format

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            raw = await to_thread(ydl.extract_info, url, download=False)
            if not isinstance(raw, dict):
                raise ParseException("yt-dlp 返回数据异常")
            return raw  # type: ignore

    @auto_task
    async def ytdlp_download_video(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        info = await self.ytdlp_extract_info(
            url, cookiefile=cookiefile, headers=headers, proxy=proxy
        )
        if info.duration > self.cfg.max_duration:
            raise DurationLimitException

        video_path = self.cfg.cache_dir / generate_file_name(url, ".mp4")
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(video_path),
            "merge_output_format": "mp4",
            # "format": f"bv[filesize<={info.duration // 10 + 10}M]+ba/b[filesize<={info.duration // 8 + 10}M]",
            # "format": "bv*[height<=720]+ba/b[height<=720]",
            "format": format or "best",
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
        }
        if loc := _ffmpeg_location():
            opts["ffmpeg_location"] = loc
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        return video_path

    @auto_task
    async def ytdlp_download_video_relaxed(
        self,
        url: str,
        *,
        cookiefile: Path | None = None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
        node: bool = False,
    ) -> Path:
        file_stem = generate_file_name(url)
        video_path = self.cfg.cache_dir / f"{file_stem}.mp4"
        if video_path.exists():
            return video_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_stem) + ".%(ext)s",
            "merge_output_format": "mp4",
            "format": format or None,
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
            "http_headers": headers or self.default_headers,
            "quiet": True,
            "no_warnings": True,
        }
        if loc := _ffmpeg_location():
            opts["ffmpeg_location"] = loc
        if not opts["format"]:
            opts.pop("format")
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)
        if node:
            opts["js_runtimes"] = {"node": {}}

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        if video_path.exists():
            return video_path

        candidates = sorted(self.cfg.cache_dir.glob(f"{file_stem}*.mp4"))
        if candidates:
            return candidates[0]
        raise DownloadException("yt-dlp 视频下载失败")

    @auto_task
    async def ytdlp_download_audio(
        self,
        url: str,
        *,
        cookiefile: Path | None,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        format: str | None = None,
    ) -> Path:
        file_name = generate_file_name(url)
        audio_path = self.cfg.cache_dir / f"{file_name}.flac"
        if audio_path.exists():
            return audio_path

        opts = {
            "outtmpl": str(self.cfg.cache_dir / file_name) + ".%(ext)s",
            "format": format or "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "flac",
                    "preferredquality": "0",
                }
            ],
            "cookiefile": None,
            "http_headers": headers or self.default_headers,
        }
        if loc := _ffmpeg_location():
            opts["ffmpeg_location"] = loc
        if proxy:
            opts["proxy"] = proxy
        if cookiefile and cookiefile.is_file():
            opts["cookiefile"] = str(cookiefile)

        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore
            await to_thread(ydl.download, [url])
        return audio_path
