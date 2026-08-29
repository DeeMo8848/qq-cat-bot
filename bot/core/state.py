# -*- coding: utf-8 -*-
"""命令开关状态管理：把每个命令的启用/停用状态持久化到 state.json。"""

import json
import os
import time

from config import ROOT

STATE_FILE = os.path.join(ROOT, "state.json")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"enabled": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_enabled(cmd_name):
    """命令是否启用（默认启用）。cmd_name 即命令函数名，如 cmd_hello。"""
    return load_state().get("enabled", {}).get(cmd_name, True)


def set_enabled(cmd_name, enabled):
    state = load_state()
    state.setdefault("enabled", {})[cmd_name] = bool(enabled)
    save_state(state)
    return bool(enabled)


# ---------- 群黑白名单 ----------
def get_group_rule(cmd_name):
    """读取某命令的群范围规则。无配置返回 {}（不限群）。"""
    return load_state().get("group_rules", {}).get(cmd_name, {}) or {}


def set_group_rule(cmd_name, mode, groups):
    """设置某命令的群范围规则。
    mode: ""=全部群  "black"=黑名单(以下群禁用)  "white"=白名单(仅以下群可用)
    groups: 群 openid 列表（自动去空去重）。
    """
    clean = []
    seen = set()
    for g in groups or []:
        g = str(g).strip()
        if g and g not in seen:
            clean.append(g)
            seen.add(g)
    state = load_state()
    rules = state.setdefault("group_rules", {})
    rules[cmd_name] = {"mode": mode or "", "groups": clean}
    save_state(state)
    return rules[cmd_name]


def allowed_in_group(cmd_name, group_openid):
    """该命令是否允许在指定群生效。group_openid 为空（非群/未知）时一律放行。"""
    if not group_openid:
        return True
    rule = get_group_rule(cmd_name)
    if not rule:
        return True
    mode = rule.get("mode")
    groups = set(rule.get("groups") or [])
    if mode == "black":
        return group_openid not in groups
    if mode == "white":
        return group_openid in groups
    return True


# ---------- B站解析模式（auto=自动解析 / passive=仅@或私聊触发） ----------
def get_bilibili_mode():
    """返回 B站解析模式："auto" 或 "passive"。"""
    return load_state().get("bilibili_mode", "auto")


def set_bilibili_mode(mode):
    """写入 B站解析模式，返回规范化后的值（仅接受 auto/passive）。"""
    state = load_state()
    state["bilibili_mode"] = "passive" if mode == "passive" else "auto"
    save_state(state)
    return state["bilibili_mode"]


# ---------- 最近群的 openid（后台填入用） ----------
_RECENT_GROUPS_MAX = 30
_RECORD_WINDOW = 1800  # 同一群 30 分钟内不重复写盘


def ensure_recent_group(group_openid):
    """记录一个最近出现过的群 openid，供后台选择填入黑白名单。"""
    if not group_openid:
        return
    state = load_state()
    rec = state.setdefault("recent_groups", {})
    now = time.time()
    if now - (rec.get(group_openid) or 0) < _RECORD_WINDOW:
        return
    rec[group_openid] = now
    if len(rec) > _RECENT_GROUPS_MAX:
        for k in sorted(rec, key=rec.get)[: len(rec) - _RECENT_GROUPS_MAX]:
            rec.pop(k, None)
    save_state(state)


def get_recent_groups():
    """返回 {openid: 最后活跃时间字符串}，按时间倒序。"""
    rec = load_state().get("recent_groups", {}) or {}
    items = sorted(rec.items(), key=lambda kv: kv[1], reverse=True)
    return {k: time.strftime("%m-%d %H:%M", time.localtime(v)) for k, v in items}