# -*- coding: utf-8 -*-
"""共享钱包（喵喵币）：所有插件统一从这里存取「喵喵币」，实现跨插件资金互通。
按用户 openid 记录余额，持久化到 data/wallet.json。纯虚拟娱乐币，不涉及真实资金。"""

import json
import os
import threading

from config import ROOT

_WALLET_FILE = os.path.join(ROOT, "data", "wallet.json")
COIN = "🐾喵喵币"
_lock = threading.Lock()


def _load() -> dict:
    if os.path.exists(_WALLET_FILE):
        try:
            with open(_WALLET_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("balances", {})
                    return data
        except Exception:
            pass
    return {"balances": {}}


def _save(data: dict):
    with open(_WALLET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _bal(data, openid) -> int:
    return int(data["balances"].get(str(openid), 0))


def balance(openid) -> int:
    with _lock:
        return _bal(_load(), openid)


def add(openid, amount) -> int:
    """入账，返回最新余额。amount 可为正（赚钱）或负（扣）。"""
    with _lock:
        data = _load()
        v = _bal(data, openid) + int(amount)
        if v < 0:
            v = 0
        data["balances"][str(openid)] = v
        _save(data)
        return v


def spend(openid, amount) -> bool:
    """扣款，余额不足返回 False。"""
    with _lock:
        data = _load()
        v = _bal(data, openid)
        if v < int(amount):
            return False
        data["balances"][str(openid)] = v - int(amount)
        _save(data)
        return True


def transfer(from_openid, to_openid, amount) -> bool:
    """转账（from -> to），成功返回 True。"""
    with _lock:
        data = _load()
        f = _bal(data, from_openid)
        if f < int(amount):
            return False
        data["balances"][str(from_openid)] = f - int(amount)
        data["balances"][str(to_openid)] = _bal(data, to_openid) + int(amount)
        _save(data)
        return True


def top(n: int = 10):
    """余额排行，返回 [(openid, 余额)...]。"""
    with _lock:
        data = _load()
        items = sorted(data["balances"].items(), key=lambda kv: -kv[1])[:n]
        return [(k, int(v)) for k, v in items if int(v) > 0]
