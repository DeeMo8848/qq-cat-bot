# -*- coding: utf-8 -*-
"""钓鱼系统配置覆盖层（Web 后台管理用）。

管理员在 Web 后台修改的参数存 data/fishing/config.json（已被 .gitignore 忽略，不入库）。
本模块负责：
- get_effective()：返回「代码默认 + 配置覆盖」后的完整视图，供后台展示编辑
- save(section, data)：把某个分区的改动写入 config.json 并立即应用到内存
- reset(section)：删除某个分区的覆盖，恢复代码默认

config.json 结构（只存被改过的分区；未出现的分区用代码默认）：
{
  "economy":  {...},   # core.py 经济参数
  "rods": {}, "hooks": {}, "lines": {}, "floats": {}, "baits": {},
  "fish": {...},       # 完整鱼种表快照（首次改动后全量保存，支持增删改）
  "gacha":  {"chance": {...}},
  "exchange": {...},
  "social": {...},
  "auto":   {...},
  "gamble": {...}
}
"""

import json
import os
import threading

from config import ROOT

_CONFIG_FILE = os.path.join(ROOT, "data", "fishing", "config.json")
_lock = threading.Lock()


# ---------- 文件读写 ----------

def _read() -> dict:
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(cfg: dict):
    os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
    tmp = _CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CONFIG_FILE)


# ---------- 默认值快照（config_store 首次导入时各模块处于原始状态） ----------

def _snapshot_defaults() -> dict:
    """各分区代码默认值的快照（config_store 首次导入时各模块处于原始状态）。"""
    from . import game, core, social, auto, gamble
    return {
        "fish": dict(game.FISH),
        "rods": dict(game.RODS),
        "hooks": dict(game.HOOKS),
        "lines": dict(game.LINES),
        "floats": dict(game.FLOATS),
        "baits": dict(game.BAITS),
        "gacha": {"chance": dict(game.GACHA_CHANCE)},
        "exchange": dict(core.EXCHANGE_GOODS),
        "economy": {
            "rod_cd": core.FISH_CD,
            "gacha_cost": core.GACHA_COST,
            "jackpot_reward": core.JACKPOT_REWARD,
            "enchant_cost": core.ENCHANT_COST,
            "unenchant_cost": core.UNENCHANT_COST,
            "market_tax": core.MARKET_TAX,
            "exchange_tax": core.EXCHANGE_TAX,
            "exchange_limit": core.EXCHANGE_LIMIT,
            "redpack_min": core.REDPACK_MIN,
            "redpack_max_count": core.REDPACK_MAX_COUNT,
        },
        "social": {
            "steal_cooldown": social.STEAL_COOLDOWN,
            "steal_rate": social.STEAL_RATE,
            "electric_cost": social.ELECTRIC_COST,
            "electric_rate": social.ELECTRIC_RATE,
            "electric_fine": social.ELECTRIC_FINE,
            "electric_cooldown": social.ELECTRIC_COOLDOWN,
            "aquarium_limit": social.AQUARIUM_LIMIT,
        },
        "auto": {
            "interval": auto.AUTO_INTERVAL,
            "cost": auto.AUTO_COST,
            "max_hours": auto.AUTO_MAX_HOURS,
        },
        "gamble": {
            "sicbo": {
                "baozi": gamble.SICBO_BAOZI_MULT,
                "even": gamble.SICBO_EVEN_MULT,
                "points": list(gamble.SICBO_POINT_MULTS),
            },
            "wheel": {
                "max": gamble.WHEEL_MAX,
                "base_rate": gamble.WHEEL_BASE_RATE,
                "rate_step": gamble.WHEEL_RATE_STEP,
                "min_rate": gamble.WHEEL_MIN_RATE,
                "factor": gamble.WHEEL_FACTOR,
            },
            "eraser": [[lo, hi, w] for lo, hi, w in gamble.ERASER_TABLE],
        },
    }


_DEFAULTS = _snapshot_defaults()


# ---------- 配置 ↔ 模块状态互转 ----------

def _fish_to_cfg(fish: dict) -> dict:
    """FISH {fid: (名, emoji, 稀有度, 基础价, (min,max), 昼夜)} → JSON 友好结构。"""
    out = {}
    for fid, (name, emoji, rarity, base, (wmin, wmax), zone) in fish.items():
        out[fid] = {"name": name, "emoji": emoji, "rarity": rarity,
                    "base": int(base), "wmin": int(wmin), "wmax": int(wmax),
                    "zone": zone}
    return out


def _cfg_to_fish(cfg: dict) -> dict:
    out = {}
    for fid, f in cfg.items():
        try:
            out[fid] = (f["name"], f["emoji"], f["rarity"], int(f["base"]),
                        (int(f["wmin"]), int(f["wmax"])), f["zone"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _gear_to_cfg(gear: dict, fields: tuple) -> dict:
    """RODS/HOOKS/LINES/FLOATS {lv: (名, 价, ...)} → JSON 结构。fields 给字段名。"""
    out = {}
    for lv, rec in gear.items():
        out[str(lv)] = dict(zip(fields, rec))
    return out


def _cfg_to_gear(cfg: dict, fields: tuple):
    out = {}
    for k, rec in cfg.items():
        try:
            key = int(k)
        except (TypeError, ValueError):
            key = k          # 鱼饵等用字符串键（bait1/bait2），保留原样
        try:
            out[key] = tuple(rec[f] for f in fields)
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------- 应用到各模块（立即生效） ----------

def apply():
    """把 config.json 的覆盖合并到各钓鱼模块，立即生效。"""
    from . import game, core, social, auto, gamble, title
    cfg = _read()

    econ = cfg.get("economy") or {}
    if econ:
        if "rod_cd" in econ:
            core.FISH_CD = int(econ["rod_cd"])
        if "gacha_cost" in econ:
            core.GACHA_COST = int(econ["gacha_cost"])
        if "jackpot_reward" in econ:
            core.JACKPOT_REWARD = int(econ["jackpot_reward"])
        if "enchant_cost" in econ:
            core.ENCHANT_COST = int(econ["enchant_cost"])
        if "unenchant_cost" in econ:
            core.UNENCHANT_COST = int(econ["unenchant_cost"])
        if "market_tax" in econ:
            core.MARKET_TAX = float(econ["market_tax"])
        if "exchange_tax" in econ:
            core.EXCHANGE_TAX = float(econ["exchange_tax"])
        if "exchange_limit" in econ:
            core.EXCHANGE_LIMIT = int(econ["exchange_limit"])
        if "redpack_min" in econ:
            core.REDPACK_MIN = int(econ["redpack_min"])
        if "redpack_max_count" in econ:
            core.REDPACK_MAX_COUNT = int(econ["redpack_max_count"])

    if "rods" in cfg:
        game.RODS = _cfg_to_gear(cfg["rods"], ("name", "price"))
    if "hooks" in cfg:
        game.HOOKS = _cfg_to_gear(cfg["hooks"], ("name", "price", "bonus"))
    if "lines" in cfg:
        game.LINES = _cfg_to_gear(cfg["lines"], ("name", "price", "mult"))
    if "floats" in cfg:
        game.FLOATS = _cfg_to_gear(cfg["floats"], ("name", "price", "bonus"))
    if "baits" in cfg:
        game.BAITS = _cfg_to_gear(cfg["baits"], ("name", "price", "bonus"))
    if "fish" in cfg:
        game.FISH = _cfg_to_fish(cfg["fish"])
        core.TOTAL_SPECIES = len(game.FISH)
        _refresh_title(core.TOTAL_SPECIES)
    if "gacha" in cfg and "chance" in cfg["gacha"]:
        game.GACHA_CHANCE = {k: float(v) for k, v in cfg["gacha"]["chance"].items()}
    if "exchange" in cfg:
        goods = {}
        for k, g in cfg["exchange"].items():
            try:
                goods[k] = (g["name"], int(g["price"]), int(g["shelf"]))
            except (KeyError, TypeError, ValueError):
                continue
        if goods:
            core.EXCHANGE_GOODS = goods

    soc = cfg.get("social") or {}
    if soc:
        if "steal_cooldown" in soc:
            social.STEAL_COOLDOWN = int(soc["steal_cooldown"])
        if "steal_rate" in soc:
            social.STEAL_RATE = float(soc["steal_rate"])
        if "electric_cost" in soc:
            social.ELECTRIC_COST = int(soc["electric_cost"])
        if "electric_rate" in soc:
            social.ELECTRIC_RATE = float(soc["electric_rate"])
        if "electric_fine" in soc:
            social.ELECTRIC_FINE = int(soc["electric_fine"])
        if "electric_cooldown" in soc:
            social.ELECTRIC_COOLDOWN = int(soc["electric_cooldown"])
        if "aquarium_limit" in soc:
            social.AQUARIUM_LIMIT = int(soc["aquarium_limit"])

    aut = cfg.get("auto") or {}
    if aut:
        if "interval" in aut:
            auto.AUTO_INTERVAL = int(aut["interval"])
        if "cost" in aut:
            auto.AUTO_COST = int(aut["cost"])
        if "max_hours" in aut:
            auto.AUTO_MAX_HOURS = int(aut["max_hours"])

    gam = cfg.get("gamble") or {}
    sicbo = gam.get("sicbo") or {}
    if sicbo:
        if "baozi" in sicbo:
            gamble.SICBO_BAOZI_MULT = int(sicbo["baozi"])
        if "even" in sicbo:
            gamble.SICBO_EVEN_MULT = int(sicbo["even"])
        if "points" in sicbo:
            gamble.SICBO_POINT_MULTS = tuple(int(x) for x in sicbo["points"])
    wheel = gam.get("wheel") or {}
    if wheel:
        if "max" in wheel:
            gamble.WHEEL_MAX = int(wheel["max"])
        if "base_rate" in wheel:
            gamble.WHEEL_BASE_RATE = float(wheel["base_rate"])
        if "rate_step" in wheel:
            gamble.WHEEL_RATE_STEP = float(wheel["rate_step"])
        if "min_rate" in wheel:
            gamble.WHEEL_MIN_RATE = float(wheel["min_rate"])
        if "factor" in wheel:
            gamble.WHEEL_FACTOR = float(wheel["factor"])
    if gam.get("eraser"):
        table = []
        for row in gam["eraser"]:
            try:
                table.append((float(row[0]), float(row[1]), float(row[2])))
            except (TypeError, ValueError, IndexError):
                continue
        if table:
            gamble.ERASER_TABLE = table


def _refresh_title(total_species: int):
    """图鉴满级档位阈值跟随鱼种数量变化。"""
    try:
        from . import title
        tiers = title.TITLE_TRACKS[2][1]
        tiers[-1] = (total_species, "全图鉴收藏家")
    except Exception:
        pass


def _apply_defaults(section: str):
    """把某个分区恢复到代码默认（reset 用），模块状态立即回到原始值。"""
    from . import game, core, social, auto, gamble
    d = _DEFAULTS.get(section)
    if d is None:
        return
    if section == "economy":
        core.FISH_CD = d["rod_cd"]
        core.GACHA_COST = d["gacha_cost"]
        core.JACKPOT_REWARD = d["jackpot_reward"]
        core.ENCHANT_COST = d["enchant_cost"]
        core.UNENCHANT_COST = d["unenchant_cost"]
        core.MARKET_TAX = d["market_tax"]
        core.EXCHANGE_TAX = d["exchange_tax"]
        core.EXCHANGE_LIMIT = d["exchange_limit"]
        core.REDPACK_MIN = d["redpack_min"]
        core.REDPACK_MAX_COUNT = d["redpack_max_count"]
    elif section in ("rods", "hooks", "lines", "floats", "baits"):
        setattr(game, section.upper(), dict(d))
    elif section == "fish":
        game.FISH = dict(d)
        core.TOTAL_SPECIES = len(d)
        _refresh_title(len(d))
    elif section == "gacha":
        game.GACHA_CHANCE = dict(d["chance"])
    elif section == "exchange":
        core.EXCHANGE_GOODS = dict(d)
    elif section == "social":
        social.STEAL_COOLDOWN = d["steal_cooldown"]
        social.STEAL_RATE = d["steal_rate"]
        social.ELECTRIC_COST = d["electric_cost"]
        social.ELECTRIC_RATE = d["electric_rate"]
        social.ELECTRIC_FINE = d["electric_fine"]
        social.ELECTRIC_COOLDOWN = d["electric_cooldown"]
        social.AQUARIUM_LIMIT = d["aquarium_limit"]
    elif section == "auto":
        auto.AUTO_INTERVAL = d["interval"]
        auto.AUTO_COST = d["cost"]
        auto.AUTO_MAX_HOURS = d["max_hours"]
    elif section == "gamble":
        gamble.SICBO_BAOZI_MULT = d["sicbo"]["baozi"]
        gamble.SICBO_EVEN_MULT = d["sicbo"]["even"]
        gamble.SICBO_POINT_MULTS = tuple(d["sicbo"]["points"])
        gamble.WHEEL_MAX = d["wheel"]["max"]
        gamble.WHEEL_BASE_RATE = d["wheel"]["base_rate"]
        gamble.WHEEL_RATE_STEP = d["wheel"]["rate_step"]
        gamble.WHEEL_MIN_RATE = d["wheel"]["min_rate"]
        gamble.WHEEL_FACTOR = d["wheel"]["factor"]
        gamble.ERASER_TABLE = [tuple(row) for row in d["eraser"]]


# ---------- 保存 / 重置 ----------

def save(section: str, data) -> bool:
    """保存某个分区的覆盖并立即应用。返回是否成功。"""
    if section not in ("economy", "rods", "hooks", "lines", "floats", "baits",
                       "fish", "gacha", "exchange", "social", "auto", "gamble"):
        return False
    with _lock:
        cfg = _read()
        cfg[section] = data
        _write(cfg)
    apply()
    return True


def reset(section: str) -> bool:
    """删除某个分区的覆盖，模块状态恢复代码默认。"""
    if section not in ("economy", "rods", "hooks", "lines", "floats", "baits",
                       "fish", "gacha", "exchange", "social", "auto", "gamble"):
        return False
    with _lock:
        cfg = _read()
        cfg.pop(section, None)
        _write(cfg)
    _apply_defaults(section)
    return True


# ---------- 读取当前生效配置（后台展示） ----------

def get_effective() -> dict:
    """返回后台可编辑的完整配置视图（代码默认 + 已应用的覆盖）。"""
    from . import game, core, social, auto, gamble
    cfg = _read()

    economy = {
        "rod_cd": core.FISH_CD,
        "gacha_cost": core.GACHA_COST,
        "jackpot_reward": core.JACKPOT_REWARD,
        "enchant_cost": core.ENCHANT_COST,
        "unenchant_cost": core.UNENCHANT_COST,
        "market_tax": core.MARKET_TAX,
        "exchange_tax": core.EXCHANGE_TAX,
        "exchange_limit": core.EXCHANGE_LIMIT,
        "redpack_min": core.REDPACK_MIN,
        "redpack_max_count": core.REDPACK_MAX_COUNT,
    }
    social_view = {
        "steal_cooldown": social.STEAL_COOLDOWN,
        "steal_rate": social.STEAL_RATE,
        "electric_cost": social.ELECTRIC_COST,
        "electric_rate": social.ELECTRIC_RATE,
        "electric_fine": social.ELECTRIC_FINE,
        "electric_cooldown": social.ELECTRIC_COOLDOWN,
        "aquarium_limit": social.AQUARIUM_LIMIT,
    }
    auto_view = {
        "interval": auto.AUTO_INTERVAL,
        "cost": auto.AUTO_COST,
        "max_hours": auto.AUTO_MAX_HOURS,
    }
    gamble_view = {
        "sicbo": {
            "baozi": gamble.SICBO_BAOZI_MULT,
            "even": gamble.SICBO_EVEN_MULT,
            "points": list(gamble.SICBO_POINT_MULTS),
        },
        "wheel": {
            "max": gamble.WHEEL_MAX,
            "base_rate": gamble.WHEEL_BASE_RATE,
            "rate_step": gamble.WHEEL_RATE_STEP,
            "min_rate": gamble.WHEEL_MIN_RATE,
            "factor": gamble.WHEEL_FACTOR,
        },
        "eraser": [[lo, hi, w] for lo, hi, w in gamble.ERASER_TABLE],
    }
    return {
        "economy": economy,
        "rods": _gear_to_cfg(game.RODS, ("name", "price")),
        "hooks": _gear_to_cfg(game.HOOKS, ("name", "price", "bonus")),
        "lines": _gear_to_cfg(game.LINES, ("name", "price", "mult")),
        "floats": _gear_to_cfg(game.FLOATS, ("name", "price", "bonus")),
        "baits": _gear_to_cfg(game.BAITS, ("name", "price", "bonus")),
        "fish": _fish_to_cfg(game.FISH),
        "gacha": {"chance": {k: v for k, v in game.GACHA_CHANCE.items()}},
        "exchange": {k: {"name": g[0], "price": g[1], "shelf": g[2]}
                     for k, g in core.EXCHANGE_GOODS.items()},
        "social": social_view,
        "auto": auto_view,
        "gamble": gamble_view,
        "overridden": sorted(cfg.keys()),
    }


# 启动时应用既有配置（webui 导入本模块时各钓鱼模块已加载、处于默认状态）
apply()
