# -*- coding: utf-8 -*-
"""VPN / 代理客户端共存保障。

问题背景
--------
bot 的群消息入口依赖 cloudflared 具名隧道（本机 → Cloudflare 边缘）。当本机
同时开着 Clash 系客户端（如猫猫云）的 **TUN 模式**时，隧道出站流量会被代理内核
接管。若内核把这些流量判给代理节点，就会因节点不支持该端口/域名而握手中断：

    WRN Unable to establish connection with Cloudflare edge
        error="TLS handshake with edge error: EOF"

表现为「bot 显示在线却收不到任何消息 + 之前的公网链接全部打不开」。

根因是「**只有域名规则、没有 IP 规则**」：TUN 模式下内核拿不到域名映射时，
cloudflared 的流量以**真实 IP** 进入内核，域名规则匹配不上，直接落进
``MATCH`` 兜底规则被丢给代理节点。

本模块做的事
------------
往代理内核里幂等注入一组「cloudflared 直连」规则（进程名 / 域名 / Cloudflare
隧道边缘 IP 段），使隧道流量在任何模式（TUN 开或关、规则或全局）下都不被代理。
订阅更新会覆盖配置文件，因此这里既改文件、也支持热重载，并带定时巡检。

安全性
------
* 幂等：重复执行不会产生重复规则，只做子集判定后补齐。
* 防御式：任何一步失败都静默降级，绝不抛出、绝不影响 bot 主流程。
* 可回滚：首次改动前自动备份为 ``config.yaml.bak-qqbot-guard``。
* 只碰 cloudflared 自己的流量，不改动其他任何代理规则。
"""

import glob
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request

from config import MIHOMO_API, MIHOMO_CONFIG, ROOT, VPN_BYPASS_ENABLED, VPN_BYPASS_INTERVAL

# ---------------------------------------------------------------- 受管规则
# 顺序有意义：进程名最可靠，域名次之，IP 段是「没有域名时」的兜底。
MANAGED_RULES = [
    "PROCESS-NAME,cloudflared.exe,DIRECT",
    "DOMAIN-SUFFIX,argotunnel.com,DIRECT",
    "DOMAIN-SUFFIX,cfargotunnel.com,DIRECT",
    "DOMAIN-SUFFIX,trycloudflare.com,DIRECT",
    "DOMAIN,api.cloudflare.com,DIRECT",
    "IP-CIDR,198.41.192.0/24,DIRECT,no-resolve",      # region1/2 边缘 v4
    "IP-CIDR,198.41.200.0/24,DIRECT,no-resolve",
    "IP-CIDR6,2606:4700:a0::/48,DIRECT,no-resolve",   # 边缘 v6
]

_TYPE_MAP = {
    "PROCESS-NAME": "ProcessName",
    "DOMAIN": "Domain",
    "DOMAIN-SUFFIX": "DomainSuffix",
    "DOMAIN-KEYWORD": "DomainKeyword",
    "IP-CIDR": "IPCIDR",
    "IP-CIDR6": "IPCIDR",
}

# 热重载会把运行配置重置为文件内容，这几个是客户端在运行时切换的开关，需要还原
_RUNTIME_KEYS = ("mode", "log-level")

_BAK_SUFFIX = ".bak-qqbot-guard"
_STATUS = {"state": "init", "detail": "", "at": ""}
_lock = threading.Lock()


def _key(rule: str):
    """把规则字符串归一化成 (类型, 匹配值, 策略) 三元组，用于幂等比对。"""
    p = [x.strip() for x in rule.split(",")]
    return (_TYPE_MAP.get(p[0].upper(), p[0]), p[1], p[2] if len(p) > 2 else "")


_MANAGED_KEYS = {_key(r) for r in MANAGED_RULES}


def _log(*a):
    print("[共存]", *a, flush=True)


# ---------------------------------------------------------------- 内核接口
def _call(api: str, method: str, ep: str, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(api + ep, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        return r.status, (json.loads(raw) if raw.strip() else None)


def _controller():
    """返回可用的内核控制地址；不是 Clash/mihomo 内核则返回 None。"""
    for api in [MIHOMO_API] + [
        "http://127.0.0.1:9790", "http://127.0.0.1:9097",
        "http://127.0.0.1:63145", "http://127.0.0.1:12333",
    ]:
        if not api or api in ():
            continue
        try:
            _, ver = _call(api, "GET", "/version", timeout=3)
            if isinstance(ver, dict) and ("meta" in ver or "premium" in ver):
                return api
        except Exception:
            continue
    return None


def _live_keys(api: str):
    _, d = _call(api, "GET", "/rules")
    return {(x.get("type"), x.get("payload"), x.get("proxy")) for x in (d or {}).get("rules", [])}


def _count_rules_in(text: str) -> int:
    lines = text.split("\n")
    ri = next((i for i, ln in enumerate(lines) if re.match(r"^rules\s*:", ln)), None)
    if ri is None:
        return -1
    n = 0
    for ln in lines[ri + 1:]:
        s = ln.strip()
        if s.startswith("- "):
            n += 1
        elif s and not s.startswith("#"):
            break
    return n


def _parse_rules(text: str):
    lines = text.split("\n")
    ri = next((i for i, ln in enumerate(lines) if re.match(r"^rules\s*:", ln)), None)
    if ri is None:
        return []
    out = []
    for ln in lines[ri + 1:]:
        s = ln.strip()
        if s.startswith("- "):
            out.append(s[2:].strip().strip("'").strip('"'))
        elif s and not s.startswith("#"):
            break
    return out


def _plausible(path: str, live_keys) -> bool:
    """候选配置判定：文件里的规则（去掉本模块自己的）应与内核在跑的规则高度重合。

    不用「规则条数相等」判定 —— 内核热重载瞬间 /rules 可能是中间状态。
    """
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return False
    rules = [r for r in _parse_rules(text) if _key(r) not in _MANAGED_KEYS]
    if len(rules) < 50:
        return False
    if not live_keys:
        return True
    hit = sum(1 for r in rules if _key(r) in live_keys)
    return hit >= len(rules) * 0.9


def _find_config(live_keys):
    """定位代理内核实际加载的配置文件（靠规则内容比对，避免猜错文件）。"""
    if MIHOMO_CONFIG and os.path.isfile(MIHOMO_CONFIG):
        return MIHOMO_CONFIG
    home = os.path.expanduser("~")
    cands = []
    for pat in (
        os.path.join(home, ".config", "*", "config.yaml"),
        os.path.join(home, ".config", "*", "config.yml"),
        os.path.join(home, ".config", "*", "*", "config.yaml"),
        os.path.join(os.environ.get("APPDATA", ""), "*", "config.yaml"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "*", "resources", "extra", "config.yaml"),
    ):
        try:
            cands += glob.glob(pat)
        except Exception:
            pass
    for p in cands:
        if _plausible(p, live_keys):
            return p
    return None


# ---------------------------------------------------------------- 配置修补
def _patch_text(src: str):
    """在 rules: 之后插入受管规则（先清掉旧的同名规则，保持幂等）。

    返回 (新内容, 被清理的旧规则条数)。
    """
    lines = src.split("\n")
    ri = next((i for i, ln in enumerate(lines) if re.match(r"^rules\s*:", ln)), None)
    if ri is None:
        raise ValueError("配置里没有 rules: 段")

    keep, removed = [], 0
    for i, ln in enumerate(lines):
        if i > ri:
            s = ln.strip()
            if s.startswith("- ") and _key(s[2:].strip().strip("'").strip('"')) in _MANAGED_KEYS:
                removed += 1
                continue
        keep.append(ln)
    ri = next(i for i, ln in enumerate(keep) if re.match(r"^rules\s*:", ln))

    # 沿用原有缩进：订阅生成的配置用 4 空格，写成 2 空格会直接 YAML 解析失败
    indent = "    "
    for ln in keep[ri + 1:ri + 8]:
        m = re.match(r"^(\s*)-\s", ln)
        if m:
            indent = m.group(1)
            break

    keep[ri + 1:ri + 1] = ["%s- '%s'" % (indent, r) for r in MANAGED_RULES]
    return "\n".join(keep), removed


def _set_status(state, detail=""):
    _STATUS.update(state=state, detail=detail, at=time.strftime("%Y-%m-%d %H:%M:%S"))


def ensure_rules(quiet=True) -> str:
    """确保 cloudflared 直连规则在内核中生效。返回状态字符串。"""
    if not VPN_BYPASS_ENABLED:
        return "disabled"
    with _lock:
        try:
            api = _controller()
            if not api:
                _set_status("no-kernel")
                return "no-kernel"

            live = _live_keys(api)
            missing = [r for r in MANAGED_RULES if _key(r) not in live]
            if not missing:
                _set_status("ok", "规则已在位")
                return "ok"

            cfg = _find_config(live)
            if not cfg:
                _set_status("no-config", "已连上内核但没定位到配置文件")
                if not quiet:
                    _log("内核在跑，但没找到配置文件；可在 settings.json 里用 MIHOMO_CONFIG 指定")
                return "no-config"

            src = open(cfg, "r", encoding="utf-8", errors="replace").read()
            crlf = "\r\n" in src
            new_src, removed = _patch_text(src)
            if crlf:
                new_src = new_src.replace("\n", "\r\n")

            bak = cfg + _BAK_SUFFIX
            if not os.path.exists(bak):
                shutil.copy2(cfg, bak)
                if not quiet:
                    _log("已备份原配置 ->", bak)

            tmp = cfg + ".tmp"
            with open(tmp, "wb") as f:
                f.write(new_src.encode("utf-8"))
            os.replace(tmp, cfg)

            # 记下运行时的开关，热重载后还原（否则会把用户的 TUN 模式关掉）
            try:
                _, cur = _call(api, "GET", "/configs")
                runtime = {k: (cur or {}).get(k) for k in _RUNTIME_KEYS}
                runtime["tun"] = {"enable": bool(((cur or {}).get("tun") or {}).get("enable"))}
            except Exception:
                runtime = None

            _call(api, "PUT", "/configs?force=true", {"path": "", "payload": new_src})

            if runtime:
                try:
                    _call(api, "PATCH", "/configs", runtime)
                except Exception:
                    pass

            time.sleep(1.0)
            still = [r for r in MANAGED_RULES if _key(r) not in _live_keys(api)]
            if still:
                # 内核热重载偶尔没吃到，补一次
                try:
                    _call(api, "PUT", "/configs?force=true", {"path": "", "payload": new_src})
                    time.sleep(1.5)
                except Exception:
                    pass
                still = [r for r in MANAGED_RULES if _key(r) not in _live_keys(api)]
            if still:
                _set_status("partial", "仍缺失: " + "; ".join(still))
                if not quiet:
                    _log("部分规则未生效:", still)
                return "partial"

            _set_status("patched", "已注入 %d 条直连规则（清理旧规则 %d 条）" % (len(MANAGED_RULES), removed))
            if not quiet:
                _log("已注入 %d 条 cloudflared 直连规则 -> %s" % (len(MANAGED_RULES), cfg))
            return "patched"
        except Exception as e:
            _set_status("error", "%s: %s" % (type(e).__name__, e))
            if not quiet:
                _log("跳过（不影响 bot 运行）:", type(e).__name__, e)
            return "error"


def status() -> dict:
    return dict(_STATUS)


# ---------------------------------------------------------------- 定时巡检
_started = False


def start_auto(interval=None):
    """后台巡检：订阅更新 / 客户端重启导致规则丢失时自动补回。"""
    global _started
    if not VPN_BYPASS_ENABLED or _started:
        return
    _started = True
    gap = int(interval or VPN_BYPASS_INTERVAL)

    def loop():
        while True:
            try:
                r = ensure_rules(quiet=True)
                if r == "patched":
                    _log("巡检发现规则缺失，已自动补回")
            except Exception:
                pass
            time.sleep(max(60, gap))

    threading.Thread(target=loop, name="vpn-bypass", daemon=True).start()
    _log("共存保障已启用（每 %d 秒巡检一次代理内核）" % max(60, gap))


if __name__ == "__main__":
    import sys
    _log("内核:", _controller() or "未找到")
    code = ensure_rules(quiet=False)
    _log("结果:", code, status())
    sys.exit(0 if code in ("ok", "patched") else 1)
