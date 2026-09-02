# -*- coding: utf-8 -*-
"""消息发送封装。

把「群聊 / 单聊 / 频道」三种场景的发消息动作统一起来，
上层代码只需要调用 send_text / send_markdown / send_media_by_url 即可，
不需要关心目标到底是群还是私聊还是频道。

消息类型(msg_type)：
    0 = 文本，2 = markdown，3 = ark 卡片，4 = embed，7 = 富媒体(media，媒体为图片时可带 content 实现图文混排)

媒体类型(file_type)：
    1 = 图片(png/jpg)，2 = 视频(mp4)，3 = 语音(silk/wav/mp3/flac)，4 = 文件
"""

from botpy.message import GroupMessage, C2CMessage, Message

import asyncio

# 场景标识常量
SCENE_GROUP = "group"   # 群聊
SCENE_C2C = "c2c"       # 单聊/私聊
SCENE_GUILD = "guild"   # 频道

# 媒体类型 file_type：1=图片 2=视频 3=语音 4=文件
FT_IMAGE = 1
FT_VIDEO = 2
FT_AUDIO = 3
FT_FILE = 4


class Sender:
    def __init__(self, api):
        """api 即 botpy 客户端的 self.api（BotAPI），负责真正调接口。"""
        self.api = api
        self._reply_seqs = {}

    def _reply_kwargs(self, message, reply, kwargs):
        """reply 发送时填 msg_id，并给递增的 msg_seq。

        官方接口默认 msg_seq=1，且「相同 msg_id + msg_seq 重复发送会失败」。
        一个流程里常对同一条消息连发多条回复（如幻影坦克的「收到里图」与
        「生成完毕」都 reply 同一张图），不加序会撞出 40054005 去重错误，
        因此按 msg_id 各自累加序号。
        """
        if reply and getattr(message, "id", None):
            mid = message.id
            n = self._reply_seqs.get(mid, 0) + 1
            self._reply_seqs[mid] = n
            if len(self._reply_seqs) > 1024:
                self._reply_seqs.clear()
            kwargs["msg_id"] = mid
            kwargs["msg_seq"] = n
        return kwargs

    # ---------- 场景识别 ----------
    def scene_of(self, message):
        """根据消息对象判断它来自哪个场景，返回 (scene, target_id)。

        - 群聊       -> ("group", group_openid)
        - 单聊       -> ("c2c",   user_openid)
        - 频道       -> ("guild", channel_id)
        - webhook 适配消息（带 group_openid / user_openid 属性的简单对象）同样识别
        """
        if isinstance(message, GroupMessage):
            return SCENE_GROUP, message.group_openid
        if isinstance(message, C2CMessage):
            return SCENE_C2C, message.author.user_openid
        if isinstance(message, Message):
            return SCENE_GUILD, message.channel_id
        # webhook 回调构造的轻量消息对象
        goid = getattr(message, "group_openid", None)
        if goid:
            return SCENE_GROUP, goid
        uoid = getattr(message, "user_openid", None)
        if uoid:
            return SCENE_C2C, uoid
        return None, None

    # ---------- 底层调用 ----------
    async def _send(self, scene, target, **kwargs):
        """按场景选择对应的发送接口。

        kwargs 里常见：
            msg_type, content, media, msg_id, message_reference, markdown, ark ...
        """
        if scene == SCENE_GROUP:
            return await self.api.post_group_message(group_openid=target, **kwargs)
        if scene == SCENE_C2C:
            return await self.api.post_c2c_message(openid=target, **kwargs)
        if scene == SCENE_GUILD:
            return await self.api.post_message(channel_id=target, **kwargs)
        raise ValueError(f"未知消息场景: {scene} / {target}")

    # ---------- 对外便捷方法（上层只用这些） ----------
    async def send_text(self, message, text, reply=True):
        """发送文本。reply=True 时以『引用那条消息』的形式回复。"""
        scene, target = self.scene_of(message)
        if not scene:
            return None
        kwargs = {"msg_type": 0, "content": text}
        if reply:
            self._reply_kwargs(message, reply, kwargs)
        return await self._send(scene, target, **kwargs)

    async def send_markdown(self, message, markdown: str, reply=False):
        """发送 markdown 富文本。注意：需要机器人在管理端具备 markdown 发送能力。"""
        scene, target = self.scene_of(message)
        if not scene:
            return None
        kwargs = {"msg_type": 2, "content": markdown}
        if reply:
            self._reply_kwargs(message, reply, kwargs)
        return await self._send(scene, target, **kwargs)

    async def send_media_by_url(self, message, file_type: int, url: str, reply=False):
        """通过公网 URL 发送图片/视频/语音到群聊或单聊。

        说明：内部先上传得到 file_info，再以富媒体消息发出。
        - file_type: 1=图片 2=视频 3=语音
        - 由于官方接口暂不支持用 URL 直接发「频道」，本方法对频道会返回提示文本。
        - 后续如需发送本地文件，请在 _upload_local 中实现「分片上传」。
        """
        scene, target = self.scene_of(message)
        if not scene:
            return None

        # 群聊 / 单聊：先用 file 接口拿到 file_info，再走富媒体消息
        if scene == SCENE_GROUP:
            media = await self.api.post_group_file(group_openid=target, file_type=file_type, url=url)
        elif scene == SCENE_C2C:
            media = await self.api.post_c2c_file(openid=target, file_type=file_type, url=url)
        else:  # 频道暂不支持 URL 上传，提示用户
            return "频道场景暂不支持用 URL 直接发媒体，请在群聊或私聊中使用。"

        kwargs = {"msg_type": 7, "media": media}
        if reply:
            self._reply_kwargs(message, reply, kwargs)
        return await self._send(scene, target, **kwargs)

    # ---------- 本地文件发送 ----------
    async def send_local_file(self, message, file_type: int, local_path: str, reply=False):
        """发送本地文件（图片/视频/语音）到群聊或单聊。

        - file_type: 1=图片 2=视频 3=语音
        - 大文件（>=10MB）走官方分片上传（支持最大 200MB），绕过 base64 10MB 限制；
          小文件优先走「URL 上传」（把文件经内网穿透暴露成公网地址），失败回退 base64。
        - 频道场景暂不支持，返回提示文本。
        """
        scene, target = self.scene_of(message)
        if not scene:
            return None
        if scene == SCENE_GUILD:
            return "频道场景暂不支持发送本地文件，请在群聊或私聊中使用。"

        import os

        size_mb = os.path.getsize(local_path) / 1024 / 1024

        # 通用文件（file_type=4，含音乐/文件卡片）与音频/语音（file_type=3）以及大文件（>=10MB）
        # 走官方分片上传：base64 旧接口对 file_type=4 常报「格式不支持/超时」，URL 上传又依赖隧道，
        # 分片直达 COS、不依赖隧道、支持任意大小，更稳。
        if file_type in (FT_FILE, FT_AUDIO) or size_mb >= 10:
            try:
                result = await self._send_local_chunked(message, scene, target, file_type, local_path, reply)
                if not isinstance(result, str):
                    return result
                return f"发送失败：{result}"
            except Exception as e:
                return f"发送失败：{e}"

        # 小文件（图片/视频等，file_type=1/2）：先 base64 直传（字节进请求体，不依赖隧道，快且稳），失败再走 URL
        try:
            result = await self._send_local_base64(message, scene, target, file_type, local_path, reply)
            if not isinstance(result, str):
                return result
            return result
        except Exception:
            pass  # base64 不适用时回退 URL

        # 兜底：URL 上传（经内网穿透暴露成公网地址；慢，但文件较小时可用）
        from bot.core import tunnel as tunnel_mod
        tunnel_url = tunnel_mod.get_url()
        if tunnel_url:
            try:
                result = await self._send_local_by_url(message, scene, target, file_type, local_path, reply, tunnel_url)
                if not isinstance(result, str):
                    return result
                return f"发送失败：{result}"
            except Exception:
                return "发送失败：媒体上传失败"
        return "发送失败：无可用上传通道"

    async def _send_local_chunked(self, message, scene, target, file_type, local_path, reply):
        """官方分片上传本地文件（支持大文件，最大约 200MB）。

        流程：upload_prepare 拿预签名 URL -> 分片 PUT 到 COS -> upload_part_finish
              -> /files 合并拿 file_info -> 发富媒体消息。
        注意：实际 API 返回的 parts[].index 从 1 开始（文档写的是 0），
              分片偏移必须用 (index - 1) * block_size，否则会读到空数据。
        """
        import hashlib
        import os

        import aiohttp
        from botpy.http import Route

        HEAD_10M = 10002432
        file_size = os.path.getsize(local_path)
        file_name = os.path.basename(local_path)

        # 计算完整文件 MD5 / SHA1 / 前 10MB 的 MD5
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        md5_10m = hashlib.md5()
        head_left = HEAD_10M
        with open(local_path, "rb") as f:
            while True:
                data = f.read(1024 * 1024)
                if not data:
                    break
                md5.update(data)
                sha1.update(data)
                if head_left > 0:
                    md5_10m.update(data[:head_left])
                    head_left -= len(data)

        # 1. upload_prepare
        if scene == SCENE_GROUP:
            prepare_route = Route("POST", "/v2/groups/{group_openid}/upload_prepare", group_openid=target)
            finish_route = Route("POST", "/v2/groups/{group_openid}/upload_part_finish", group_openid=target)
            files_route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=target)
        else:
            prepare_route = Route("POST", "/v2/users/{openid}/upload_prepare", openid=target)
            finish_route = Route("POST", "/v2/users/{openid}/upload_part_finish", openid=target)
            files_route = Route("POST", "/v2/users/{openid}/files", openid=target)

        prepare = await self.api._http.request(
            prepare_route,
            json={
                "file_type": file_type,
                "file_name": file_name,
                "file_size": str(file_size),
                "md5": md5.hexdigest(),
                "sha1": sha1.hexdigest(),
                "md5_10m": md5_10m.hexdigest(),
            },
        )
        upload_id = prepare["upload_id"]
        parts = prepare["parts"]
        default_block = int(prepare.get("block_size", HEAD_10M))

        # 2. 分片 PUT 到 COS + 3. upload_part_finish
        async with aiohttp.ClientSession() as session:
            for part in parts:
                index = part["index"]
                block_size = int(part["block_size"])
                offset = (index - 1) * default_block
                with open(local_path, "rb") as f:
                    f.seek(offset)
                    data = f.read(block_size)
                async with session.put(part["presigned_url"], data=data) as resp:
                    if resp.status not in (200, 201, 204):
                        raise RuntimeError(f"分片 {index} 上传失败: HTTP {resp.status}")
                await self.api._http.request(
                    finish_route,
                    json={
                        "upload_id": upload_id,
                        "part_index": index,
                        "block_size": str(len(data)),
                        "md5": hashlib.md5(data).hexdigest(),
                    },
                )

        # 4. 合并获取 file_info
        # QQ 端有时还没把分片拼好就立刻合并会报「上传超时」(850027)，此时等一会重试即可
        kwargs = {"file_type": file_type, "upload_id": upload_id, "file_name": file_name, "srv_send_msg": False}
        media = None
        for attempt in range(4):
            try:
                media = await self.api._http.request(files_route, json=kwargs)
                break
            except Exception as e:
                msg = str(e)
                if attempt < 3 and ("850027" in msg or "超时" in msg):
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
        if media is None:
            raise RuntimeError("分片合并获取 file_info 失败")

        # 5. 发送富媒体消息
        msg_kwargs = {"msg_type": 7, "media": media}
        if reply:
            self._reply_kwargs(message, reply, msg_kwargs)
        return await self._send(scene, target, **msg_kwargs)

    async def _send_local_by_url(self, message, scene, target, file_type, local_path, reply, tunnel_url):
        """把本地文件复制到媒体目录，用公网 URL 走官方上传接口。"""
        import os
        import shutil
        import uuid

        from bot.core.webhook import MEDIA_DIR

        os.makedirs(MEDIA_DIR, exist_ok=True)
        filename = uuid.uuid4().hex + os.path.splitext(local_path)[1]
        dest = os.path.join(MEDIA_DIR, filename)
        try:
            shutil.copyfile(local_path, dest)
            public_url = f"{tunnel_url.rstrip('/')}/media/{filename}"
            if scene == SCENE_GROUP:
                media = await self.api.post_group_file(
                    group_openid=target, file_type=file_type, url=public_url, srv_send_msg=False
                )
            else:
                media = await self.api.post_c2c_file(
                    openid=target, file_type=file_type, url=public_url, srv_send_msg=False
                )
            kwargs = {"msg_type": 7, "media": media}
            if reply:
                self._reply_kwargs(message, reply, kwargs)
            return await self._send(scene, target, **kwargs)
        finally:
            try:
                os.remove(dest)
            except Exception:
                pass

    async def _send_local_base64(self, message, scene, target, file_type, local_path, reply):
        """base64 上传本地文件（旧接口，约 10MB 上限）。"""
        import base64

        with open(local_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        from botpy.http import Route

        if scene == SCENE_GROUP:
            route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=target)
        else:
            route = Route("POST", "/v2/users/{openid}/files", openid=target)

        try:
            media = await self.api._http.request(
                route,
                json={"file_type": file_type, "file_data": b64, "srv_send_msg": False},
            )
            kwargs = {"msg_type": 7, "media": media}
            if reply:
                self._reply_kwargs(message, reply, kwargs)
            return await self._send(scene, target, **kwargs)
        except Exception as e:
            return f"发送失败：{e}"

    async def send_image_with_text(self, message, text: str, local_path: str, reply=False):
        """发送「文字 + 本地图片」的图文混排消息（msg_type=7 富媒体），一条消息同时含文字和图片。"""
        scene, target = self.scene_of(message)
        if not scene:
            return None
        if scene == SCENE_GUILD:
            return "频道场景暂不支持发送本地文件，请在群聊或私聊中使用。"

        import base64

        with open(local_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        from botpy.http import Route

        if scene == SCENE_GROUP:
            route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=target)
        else:
            route = Route("POST", "/v2/users/{openid}/files", openid=target)

        try:
            media = await self.api._http.request(
                route,
                json={"file_type": 1, "file_data": b64, "srv_send_msg": False},
            )
            kwargs = {"msg_type": 7, "content": text, "media": media}
            if reply:
                self._reply_kwargs(message, reply, kwargs)
            return await self._send(scene, target, **kwargs)
        except Exception as e:
            return f"发送失败：{e}"