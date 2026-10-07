# -*- coding: utf-8 -*-
"""高峰时段钱包保护（移植自 astrbot_plugin_fat_fish_wallet）。

高峰时段自动禁用 AI 对话，空闲时段自动恢复；并在高峰起/止向最近活跃的群
广播一句随机的休息/开工提醒。

时段定义（北京时区）：
  高峰 = 周一至周五（不含中国法定节假日）内 09:00-12:00、14:00-18:00；
  其余（含周末、中国法定节假日全天）均为空闲。

节假日判断优先用 `chinesecalendar` 库；没装时退化为“仅工作日 + 时段”判断
（此时工作日里恰好是法定假日的那几天不会被排除，属可接受降级）。

运行环境：本模块与 bot 同进程同事件循环，提醒协程由 main.py 启动。
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import ROOT

_log = logging.getLogger("peak_guard")

_TZ = ZoneInfo("Asia/Shanghai")

# 高峰时段（当天秒数，含开始不含结束）：09:00-12:00、14:00-18:00
DEFAULT_PERIODS = "09:00-12:00,14:00-18:00"
# 高峰只针对工作日（0=周一 … 4=周五）
DEFAULT_WEEKDAYS = "0,1,2,3,4"

PEAK_START_LINES = [
    "笨蛋还不走，梁文峰时间到了，本喵休息了喵！",
    "哼，还加班呢，梁文峰时间到了，别卷了喵！",
    "梁文峰时间到，钱包瑟瑟发抖，本喵先躲起来睡个午觉喵～",
    "高峰时段，脑子太贵，本喵关张半小时再回来喵！",
    "辛苦了辛苦了，梁文峰时间到了，放自己一马，本喵也在摸鱼喵～",
]
PEAK_END_LINES = [
    "梁文谷时间到，本喵满血复活，开蹬喵！",
    "梁文谷时间开始，本喵批准你今天到此为止喵！",
    "梁文谷时间到，周末就该躺平，本喵盯着你呢喵！",
    "空闲时段到啦，本喵出山，随叫随到喵～",
    "梁文峰走了，梁文谷来了，本喵伸个懒腰开始干活喵！",
]


@dataclass(frozen=True)
class _Period:
    start: int  # 当天秒数，含
    end: int    # 当天秒数，不含

    def contains(self, t: int) -> bool:
        return self.start <= t < self.end


def _to_seconds(hhmm: str) -> int:
    h, m = hhmm.strip().split(":", 1)
    return int(h) * 3600 + int(m) * 60


def parse_periods(spec: str) -> list:
    """解析 '09:00-12:00,14:00-18:00'，非法段忽略。返回 [_Period]（优先模块内类型）。"""
    out = []
    if not spec:
        return out
    for part in str(spec).split(","):
        part = part.strip()
        if "-" not in part:
            continue
        try:
            start = _to_seconds(part.split("-", 1)[0])
            end = _to_seconds(part.split("-", 1)[1])
        except (ValueError, IndexError):
            continue
        if end > start:
            out.append(_Period(start, end))
    return out


def parse_weekdays(spec: str) -> list:
    """解析 '0,1,2,3,4'（0=周一…6=周日）；空或非法一律视为“不限”。"""
    days = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.append(int(part))
    return days


def _day_seconds(dt: datetime) -> int:
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _is_holiday(dt: datetime) -> bool:
    """中国法定节假日（含周末口径由 chinesecalendar 判定）。"""
    try:
        import chinese_calendar as _cc
        return bool(_cc.is_holiday(dt.date()))
    except Exception:
        return False


def is_peak(now: datetime | None = None,
            periods=None, weekdays=None, consider_holiday: bool = True) -> bool:
    """此刻是否为高峰时段。

    - 非工作日（默认周一~周五之外）→ 否
    - 中国法定节假日（落在工作日的调休放假）→ 否
    - 否则看当天秒数是否落在高峰时段内
    """
    now = now or datetime.now(_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_TZ)
    days = parse_weekdays(DEFAULT_WEEKDAYS if weekdays is None else weekdays)
    if days and now.weekday() not in days:
        return False
    if consider_holiday and _is_holiday(now):
        return False
    ps = parse_periods(DEFAULT_PERIODS if periods is None else periods)
    if not ps:
        return False
    t = _day_seconds(now)
    return any(p.contains(t) for p in ps)


def is_peak_blocked(now: datetime | None = None) -> bool:
    """是否应在当前时刻拦截 AI（先看总开关，再看时段）。"""
    from config import PEAK_GUARD_ENABLED
    if not PEAK_GUARD_ENABLED:
        return False
    return is_peak(now)


def next_transition(now, periods=None, weekdays=None) -> tuple[str, datetime]:
    """返回 (下一个时段, 切换时刻)。9 天内没有则返回 30 天后。"""
    days = parse_weekdays(DEFAULT_WEEKDAYS if weekdays is None else weekdays)
    ps = parse_periods(DEFAULT_PERIODS if periods is None else periods)
    tz = now.tzinfo or _TZ
    for off in range(0, 10):
        base = (now + timedelta(days=off)).replace(hour=0, minute=0, second=0, microsecond=0)
        boundaries = [base]
        if days and with_holiday_weekday(base, days):
            for p in ps:
                boundaries.append(base + timedelta(seconds=p.start))
                boundaries.append(base + timedelta(seconds=p.end))
        for b in sorted(boundaries):
            if b <= now:
                continue
            before = b - timedelta(seconds=1)
            if is_peak(before, periods, weekdays) != is_peak(b, periods, weekdays):
                return ("peak" if is_peak(b, periods, weekdays) else "offpeak", b)
    return ("offpeak", now + timedelta(days=30))


def with_holiday_weekday(base: datetime, days: list) -> bool:
    """base（当天 0 点）是否属于“可能产生高峰”的日期（工作日且非节假日）。"""
    if days and base.weekday() not in days:
        return False
    return not _is_holiday(base)


def current_status() -> dict:
    """供 WebUI / 测试展示当前时段信息。"""
    from config import PEAK_GUARD_ENABLED
    now = datetime.now(_TZ)
    peak = is_peak(now)
    target, when = next_transition(now)
    return {
        "enabled": bool(PEAK_GUARD_ENABLED),
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": now.weekday(),
        "is_holiday": _is_holiday(now),
        "peak": peak,
        "period": "peak" if peak else "offpeak",
        "blocked": is_peak_blocked(now),
        "next_kind": target,
        "next_at": when.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------- 定时提醒 ----------

def start_reminder_loop(api) -> asyncio.Task:
    """启动后台提醒协程：高峰起/止向最近活跃的群广播随机提醒语。"""
    loop = asyncio.get_running_loop()
    return loop.create_task(_reminder_loop(api))


async def _reminder_loop(api):
    from config import PEAK_REMINDER_ENABLED
    last_state = None      # True=高峰, False=空闲, None=还没判定过
    _suffix_cache = {}     # 记录每个 key 最后发送的日期，避免一天内重复
    try:
        while True:
            try:
                if PEAK_REMINDER_ENABLED:
                    now = datetime.now(_TZ)
                    cur = is_peak(now)
                    if last_state is not None and cur != last_state:
                        await _broadcast_transition(api, cur)
                    last_state = cur
            except Exception as e:
                _log.error("[峰谷提醒] 循环异常: %s", e)
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        pass


async def _broadcast_transition(api, into_peak: bool):
    """into_peak=True 发送“高峰开始”，False 发送“高峰结束/空闲开始”。"""
    from bot.core import state
    lines = PEAK_START_LINES if into_peak else PEAK_END_LINES
    groups = list(state.get_recent_groups().keys())
    if not groups:
        _log.info("[峰谷提醒] 无已知群，跳过 %s 提醒", "高峰开始" if into_peak else "空闲开始")
        return
    sent = 0
    for gid in groups:
        try:
            line = random.choice(lines)
            await api.post_group_message(group_openid=gid, msg_type=0, content=line)
            sent += 1
        except Exception as e:
            _log.warning("[峰谷提醒] 发送到 %s 失败: %s", gid, e)
    _log.info("[峰谷提醒] 已向 %d/%d 个群发送%s", sent, len(groups),
              "高峰开始" if into_peak else "空闲开始")