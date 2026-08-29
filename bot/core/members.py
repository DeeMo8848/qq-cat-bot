# -*- coding: utf-8 -*-
"""群成员头像/昵称获取。

头像：用 QQ 官方跨应用头像直链，无需申请「获取群成员」接口权限：
    https://q.qlogo.cn/qqapp/{appid}/{openid}/0
（与 astrbot_plugin_memelite 的 core/avatar.py 同款做法，仅需 bot 的 appid 与用户 openid。）

昵称：从消息事件作者里直接带（author.username），故这里不再需要单独接口。
"""

from config import APPID


def get_member_avatar_url(member_openid: str) -> str:
    """根据 appid + openid 构造公开头像直链；openid 缺失返回空串。"""
    if not member_openid:
        return ""
    return f"https://q.qlogo.cn/qqapp/{APPID}/{member_openid}/0"


async def get_member_avatar(api, group_openid: str, member_openid: str) -> str:
    """返回某成员头像 URL；拿不到返回空串。api/group 仅兼容旧签名，实际不用。"""
    return get_member_avatar_url(member_openid)


async def get_member_nick(api, group_openid: str, member_openid: str) -> str:
    """昵称改从消息作者 username 获取，此处保持签名兼容返回空串。"""
    return ""