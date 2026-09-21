from re import Match
from typing import ClassVar

from aiohttp import ClientError

from .._log import logger
from ..config import PluginConfig
from ..cookie import CookieJar
from ..data import Platform
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle


class NCMParser(BaseParser):
    """网易云音乐解析器"""

    platform: ClassVar[Platform] = Platform(name="ncm", display_name="网易云")

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.headers.update({"Referer": "https://music.163.com"})
        self.mycfg = config.parser.ncm
        self.cookiejar = CookieJar(config, self.mycfg, domain="music.163.com")
        self._sync_cookie_header()

    def _sync_cookie_header(self) -> None:
        """把 cookiejar 里的 cookie 同步进请求头（每次解析前调用，避免快照过期）。

        ★ 教训：原来只在 __init__ 里塞一次 cookie，若登录态随后更新
        （update_from_response / 用户重新配置），实例仍拿着旧值。
        """
        jar = self.cookiejar
        # 优先用完整的 cookie 串；没有则按域名拼一条
        cookies_str = (jar.cookies_str or "").strip()
        if not cookies_str:
            cookies_str = jar.get_cookie_header(domain="music.163.com").strip()
        if cookies_str:
            self.headers["cookie"] = cookies_str
        else:
            self.headers.pop("cookie", None)

    @property
    def has_cookie(self) -> bool:
        """是否带上了登录态（用于日志/排障，判断是不是在「裸奔」）。"""
        return bool(self.headers.get("cookie"))

    @handle("163cn.tv", r"163cn\.tv/(?P<short_key>\w+)")
    async def _parse_short(self, searched: Match[str]):
        short_url = f"https://163cn.tv/{searched.group('short_key')}"
        # 让框架跟随 302 后再走通用解析
        return await self.parse_with_redirect(short_url)

    @handle("y.music.163.com", r"y\.music\.163\.com/m/song\?.*id=(?P<song_id>\d+)")
    @handle("music.163.com", r"music\.163\.com/#/song\?.*id=(?P<song_id>\d+)")
    @handle("music.163.com", r"music\.163\.com/song\?.*id=(?P<song_id>\d+)")
    async def _parse_song(self, searched: Match[str]):
        song_id = searched.group("song_id")
        # 每次解析前刷新 cookie（登录态可能已更新）
        self._sync_cookie_header()
        if not self.has_cookie:
            logger.warning(
                "[NCM] 未携带 cookie 解析（settings.json 的 NETEASE_COOKIE 为空）"
                "—— 免费曲也可能拿不到播放地址"
            )
        detail_url = (
            f"https://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
        )
        # br 用 128k：压到 10MB 以内，避免走分片上传（那条路径易失败），且 QQ 语音会再转码
        play_url = f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=128000"

        # 1. 取歌曲元数据
        async with self.session.get(detail_url, headers=self.headers) as resp:
            if resp.status >= 400:
                raise ClientError(f"[NCM] 获取歌曲信息失败 HTTP {resp.status}")
            # content_type=None：网易云接口常回 text/plain（但正文是合法 JSON），跳过类型校验
            detail_json = await resp.json(content_type=None)
            logger.debug("[NCM] 歌曲信息: %s", detail_json)

        song = detail_json.get("songs", [{}])[0]
        if not song:
            raise ValueError("[NCM] 未找到该歌曲")

        title = song.get("name", "")
        sub_title = (song.get("alias") or [""])[0]  # 别名
        album_name = song.get("album", {}).get("name", "")
        cover_url = song.get("album", {}).get("picUrl", "") + "?param=640y640"
        duration_ms = song.get("duration", 0)

        # 作者信息
        ar_list = song.get("artists") or []
        author_name = " / ".join(ar.get("name", "") for ar in ar_list)
        author_avatar = next(iter(ar_list), {}).get("img1v1Url", "")

        # 2. 取播放地址：enhance 官方接口 → outer 外链兜底
        # ★ 固定 128k（不再因有 cookie 就升 320k）：
        #   · QQ 收到音频后会再转码成语音，320k 对最终听感几乎没有增益
        #   · 320k 单曲可达 12MB+，在服务器带宽下下载耗时很长，用户会以为「bot 卡死了」
        #   · 128k 约 3~4MB，下载快很多；需要高码率可改这里的常量
        br = 128000
        play_url = (
            f"https://music.163.com/api/song/enhance/player/url"
            f"?ids=[{song_id}]&br={br}"
        )
        audio_url = ""
        play_code = None
        try:
            async with self.session.get(play_url, headers=self.headers) as resp:
                if resp.status >= 400:
                    logger.warning("[NCM] 获取播放地址 HTTP %s", resp.status)
                else:
                    play_json = await resp.json(content_type=None)
                    play_info = (play_json.get("data") or [{}])[0]
                    play_code = play_info.get("code")
                    # ★ 无版权 / 需付费的曲子，这个字段是 **JSON null**（不是空字符串）。
                    #   切记用 `or ""`：`dict.get(k, "")` 在「键存在且值为 None」时返回 None，
                    #   default 不生效 —— 曾因此让 path_task=None，发送阶段炸出
                    #   "object NoneType can't be used in 'await' expression"，用户侧表现为
                    #   「发了链接什么反应都没有」。
                    audio_url = play_info.get("url") or ""
        except Exception as e:
            logger.warning("[NCM] enhance 接口异常: %s", e)

        if not audio_url:
            # outer 外链：非 VIP / 免费曲常可用（会 302 到真实 mp3）
            audio_url = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
            logger.info(
                "[NCM] enhance 未给出播放地址（code=%s），改用 outer 外链兜底", play_code
            )

        if not audio_url:
            raise ParseException(
                f"[NCM] 《{title}》没有可用的播放地址"
                f"（code={play_code}，通常是 VIP/无版权曲目）"
            )

        # 3. 组装结果（必须是 AudioContent：否则会被误当作视频、以 file_type=2 发送导致「格式不支持」）
        author = self.create_author(author_name, author_avatar)
        audio = self.create_audio_content(
            audio_url, duration=duration_ms // 1000, cover_url=cover_url
        )

        # 4. 返回
        return self.result(
            title=f"{title}{'（' + sub_title + '）' if sub_title else ''}",
            text=f"专辑：{album_name}",
            author=author,
            contents=[audio],
            timestamp=None,
            url=f"https://music.163.com/#/song?id={song_id}",
        )

    # 3. 直链 mp3 —— 直接下载
    @handle("music.126.net", r"https?://[^/]*music\.126\.net/.*\.mp3(?:\?.*)?$")
    async def _parse_direct_mp3(self, searched: Match[str]):
        url = searched.group(0)  # 整条 url
        audio = self.create_audio_content(url)
        return self.result(
            title="网易云音乐",
            text="直链音频",
            contents=[audio],
            url=url,
        )

    @handle(
        "music.163.com/song/media/outer/url",
        r"(https?://music\.163\.com/song/media/outer/url\?[^>\s]+)",
    )
    async def _parse_private_outer(self, searched: Match[str]):
        # 整条原始 URL 就是直链
        private_url = searched.group(0)
        audio = self.create_audio_content(private_url)
        return self.result(
            title="网易云音乐（私人直链）",
            text="直链音频",
            contents=[audio],
            url=private_url,
        )
