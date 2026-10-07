# -*- coding: utf-8 -*-
"""回调按钮（action.type=1）行为实验处理器。

背景：用户点击回调按钮 → 平台推 `INTERACTION_CREATE` → 官方要求 bot 在有效时间内调
`PUT /interactions/{id}` 回应。但「回应之后用户端到底能看到什么」有多种可能，
本模块把候选方式做成对照实验，**一次点击只测一种**。

button_data 约定：`mdtest:<exp>`，exp 见 `HANDLERS`。

★ 本实验要回答的核心问题：**回调按钮点击后，bot 能不能主动产出消息？**
  官方「消息收发概述」把被动消息分为两类：
    - 被动消息(回复用户)：携带 `msg_id`
    - 被动消息(响应事件)：携带 **`event_id`**
  互动事件的 id 属于后者。早期误用 `msg_id=iid` 发消息被平台拒绝
  （400 `code=40034024 请求参数msg_id无效或越权`），所以这次改用 `event_id=`。
"""

import logging

# ★ 注意：这里不能写成「函数名与全局变量同名」的懒加载写法 ——
#   `def _log()` 体内 `global _log` 会让 `if _log is None` 永远为假（它此刻是函数对象），
#   于是 `_log()` 返回函数本身，`.error()` 直接报 'function' object has no attribute 'error'。
#   （2026-10-08 踩过：异常分支全被这个 bug 吞掉，日志里只剩它自己。）
_log = logging.getLogger("md_test")


async def _send(api, d, **kwargs):
    """按事件场景选择群聊 / 单聊发送接口。"""
    if d.get("group_openid"):
        return await api.post_group_message(group_openid=d["group_openid"], **kwargs)
    if d.get("user_openid"):
        return await api.post_c2c_message(openid=d["user_openid"], **kwargs)
    return None


async def _exp_event(api, d, iid, resolved):
    """① 用 event_id 作被动回复凭据发文本（官方「被动消息(响应事件)」写法）。"""
    await _send(api, d, msg_type=0, event_id=iid,
                content="✅ 方式① 成功：以 event_id 作凭据回复（官方「被动消息(响应事件)」写法）")


async def _exp_active(api, d, iid, resolved):
    """② 不带任何 id 直接发（主动消息）。"""
    await _send(api, d, msg_type=0,
                content="✅ 方式② 成功：不带 msg_id / event_id 直接发（主动消息）")


async def _exp_md(api, d, iid, resolved):
    """③ 用 event_id 发一条 markdown。"""
    await _send(api, d, msg_type=2, event_id=iid,
                markdown={"content": "# ✅ 方式③ 成功\n用 **event_id** 回的 markdown\n\n"
                                     "> 说明回调按钮可以直接产出富文本"})


async def _exp_echo(api, d, iid, resolved):
    """④ 回显 button_data（验证数据透传）。"""
    await _send(api, d, msg_type=0, event_id=iid,
                content="✅ 方式④ 收到 button_data = %r" % (resolved.get("button_data"),))


HANDLERS = {
    "event": _exp_event,
    "active": _exp_active,
    "md": _exp_md,
    "echo": _exp_echo,
}


async def handle(api, d, iid, resolved, exp):
    """按实验代号分发。"""
    fn = HANDLERS.get(exp)
    if not fn:
        _log.warning("[md_test] 未知回调实验代号: %r", exp)
        return
    try:
        await fn(api, d, iid, resolved)
        print("[md_test] 回调实验 %r 已执行（互动 id=%s）" % (exp, iid), flush=True)
    except Exception as e:
        _log.error("[md_test] 回调实验 %r 失败: %s", exp, e)
