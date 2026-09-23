# -*- coding: utf-8 -*-
"""修补 botpy 网关的重连缺陷 —— 防止「4009 死循环」把机器人拖死。

## 问题（2026-09-24 线上实测）

botpy 的 `BotWebSocket` 有两处设计缺陷，叠加后会让机器人**永久失去收消息能力**：
进程活着、端口正常监听、日志还在滚，但一条消息都进不来，重启前不会自愈。

### 缺陷 1：`_INVALID_RECONNECT_CODE` 漏了 4009

```python
_INVALID_RECONNECT_CODE = [9001, 9005]        # 没有 4009 / 1006
```

`on_closed` 只在这些码出现时才清空 `session_id`：

```python
if close_status_code in self._INVALID_RECONNECT_CODE or not self._can_reconnect:
    self._session["session_id"] = ""
    self._session["last_seq"] = 0
self._connection.add(self._session)
```

于是收到 `4009 Session timed out` 后，botpy 会带着**已经失效的 session** 一直发
`ws_resume`。服务端口头回一个 RESUMED（日志打出「机器人重连成功!」），随即再以
4009 踢掉 —— 形成「重连成功 → 心跳维持启动 → 4009 → 重连」的无限循环。

协议上 4009 的语义就是「连接过期，请重新连接」，**必须重新 Identify，不能 Resume**。

### 缺陷 2：`_send_heart` 抛异常后静默死亡

`_send_heart` 里 `await self.send_msg(...)` 没有 try/except。当底层连接已进入
closing、但 `self._conn.closed` 仍是 False 时，`send_msg` 会抛
`ClientConnectionResetError('Cannot write to closing transport')`，心跳协程当场
终止，事件循环只留下一条无人接管的 `Task exception was never retrieved`。
此后**再没有任何东西发送心跳**，服务端只能按超时踢人（就是那个 4009）。

## 修补方式

- 把 4009 / 1006 / 4008 / 4006 补进 `_INVALID_RECONNECT_CODE`：这些码出现时清空
  session 并重新 Identify，从而打破死循环；
- 给 `_send_heart` 包一层：发送失败时主动关闭底层连接，让 `ws_connect` 的接收
  循环退出并触发 `on_closed`，走上「清空 session + 重新 Identify」的正路，
  而不是等一个完整心跳周期后被服务端踢。

本模块幂等，必须在 `botpy.Client` 实例化**之前**调用 `install()`。
"""

import json

import botpy.gateway as _gateway

# 4009 Session timed out（连接过期）、1006 连接异常关闭（无 close frame）、
# 4006 无效 session、4008 发送过快 —— 这几个都不该走 Resume。
_EXTRA_INVALID_RECONNECT_CODES = (4009, 1006, 4008, 4006)

_PATCH_MARK = "_wb_resilience_patched"


def _patch_reconnect_codes() -> bool:
    """把 4009 / 1006 等「必须重建会话」的关闭码补进 botpy 的白名单。"""
    cls = _gateway.BotWebSocket
    codes = list(getattr(cls, "_INVALID_RECONNECT_CODE", []) or [])
    added = [c for c in _EXTRA_INVALID_RECONNECT_CODES if c not in codes]
    if not added:
        return False
    cls._INVALID_RECONNECT_CODE = codes + added
    print("[resilience] 已扩充 botpy 重连码 _INVALID_RECONNECT_CODE: +%s" % added,
          flush=True)
    return True


async def _send_heart_resilient(self, interval):
    """`BotWebSocket._send_heart` 的加固版：发送失败不再让协程静默死掉。"""
    _gateway._log.info("[botpy] 心跳维持启动...")
    while True:
        if self._conn is None:
            _gateway._log.debug("[botpy] 连接已关闭!")
            return
        if self._conn.closed:
            _gateway._log.debug(
                "[botpy] ws连接已关闭, 心跳检测停止，ws对象: %s" % self._conn)
            return
        try:
            await self.send_msg(json.dumps({
                "op": self.WS_HEARTBEAT,
                "d": self._session["last_seq"],
            }))
        except Exception as exc:  # noqa: BLE001 —— 任何发送失败都说明这条连接已废
            _gateway._log.error(
                "[resilience] 心跳发送失败(%s: %s)，主动关闭连接以触发会话重建"
                % (type(exc).__name__, exc))
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001 —— 关不掉也无所谓，下面照样退出
                pass
            return
        await _gateway.asyncio.sleep(interval)


def install(verbose: bool = True) -> bool:
    """安装补丁。幂等，可重复调用；返回 True 表示本次确实做了修改。"""
    if getattr(_gateway.BotWebSocket, _PATCH_MARK, False):
        # 标记已打过，但类属性可能被其它代码重置 —— 仍然校验一次重连码
        _patch_reconnect_codes()
        return False

    changed = _patch_reconnect_codes()

    # 只在签名匹配时替换心跳函数，避免 botpy 升级后误伤。
    original = getattr(_gateway.BotWebSocket, "_send_heart", None)
    if callable(original) and original is not _send_heart_resilient:
        try:
            _gateway.BotWebSocket._send_heart = _send_heart_resilient
            changed = True
        except Exception as exc:  # noqa: BLE001
            _gateway._log.error("[resilience] 心跳加固替换失败（已忽略）: %s" % exc)

    if changed:
        setattr(_gateway.BotWebSocket, _PATCH_MARK, True)
        if verbose:
            print("[resilience] botpy 网关重连缺陷补丁已生效", flush=True)
    return changed
