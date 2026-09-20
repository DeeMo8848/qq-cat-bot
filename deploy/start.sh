#!/usr/bin/env bash
# =====================================================================
#  start.sh — QQ 机器人 Linux 启动脚本
# ---------------------------------------------------------------------
#  用法：
#     bash deploy/start.sh              # 前台运行（Ctrl+C 退出）
#     bash deploy/start.sh --daemon     # 后台运行，日志写入 logs/bot.log
#     bash deploy/start.sh --restart    # 先停掉已有的再启动
#
#  与 Windows 的「启动bot.bat」对应：自动定位可用的 Python 解释器
#  （优先用装了 botpy 的那个），然后运行 main.py。
# =====================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

DAEMON=0
RESTART=0
ACTION="run"
for arg in "$@"; do
  case "$arg" in
    --daemon|-d) DAEMON=1 ;;
    --restart) RESTART=1 ;;
    stop|--stop) ACTION="stop" ;;
    start) ACTION="run" ;;
    *) ;;
  esac
done

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'

PID_FILE="$ROOT/.bot.pid"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/bot.log"

# ---------- 查找 bot 进程 ----------
# 优先 pgrep；无 pgrep 时扫 /proc（Linux 一定有），保证脚本在各种精简环境可用
find_bot_pids() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "python.*main\.py" 2>/dev/null | grep -v "^$$\$" || true
    return
  fi
  local p pid cmd
  for p in /proc/[0-9]*; do
    [[ -d "$p" ]] || continue
    pid="${p#/proc/}"
    [[ "$pid" == "$$" ]] && continue
    cmd="$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null || true)"
    if [[ "$cmd" == *python* && "$cmd" == *main.py* ]]; then
      echo "$pid"
    fi
  done
}

# ---------- 停止已有实例 ----------
stop_existing() {
  if [[ -f "$PID_FILE" ]]; then
    local old; old="$(cat "$PID_FILE" 2>/dev/null)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
      echo "    停止旧进程 PID=$old"
      kill "$old" 2>/dev/null
      for _ in $(seq 1 10); do
        kill -0 "$old" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$old" 2>/dev/null && kill -9 "$old" 2>/dev/null
    fi
    rm -f "$PID_FILE"
  fi
  # 兜底：PID 文件不存在时按命令行扫一遍，避免残留进程占端口
  local strays
  strays="$(find_bot_pids)"
  if [[ -n "$strays" ]]; then
    echo "    清理残留进程：$strays"
    echo "$strays" | xargs -r kill 2>/dev/null || true
    sleep 1
    strays="$(find_bot_pids)"
    [[ -n "$strays" ]] && echo "$strays" | xargs -r kill -9 2>/dev/null || true
  fi
}

# 纯停止：不需要解释器，提前返回
if [[ "$ACTION" == "stop" ]]; then
  echo -e "${GREEN}停止 QQ 机器人${NC}"
  stop_existing
  echo -e "${GREEN}    已停止${NC}"
  exit 0
fi

# ---------- 挑选解释器：优先有 botpy 的 ----------
pick_python() {
  local cands=()
  # 1) 虚拟环境（install.sh 未强制创建，但用户可能自建）
  for v in "$ROOT/.venv/bin/python" "$ROOT/venv/bin/python"; do
    [[ -x "$v" ]] && cands+=("$v")
  done
  # 2) 常见解释器名
  cands+=(python3 python)
  for c in "${cands[@]}"; do
    local p
    if [[ "$c" == /* ]]; then p="$c"; else p="$(command -v "$c" 2>/dev/null)"; fi
    [[ -n "$p" && -x "$p" ]] || continue
    if "$p" -c 'import botpy' >/dev/null 2>&1; then
      echo "$p"; return 0
    fi
  done
  # 3) 退而求其次：任意 python3
  for c in python3 python; do
    local p; p="$(command -v "$c" 2>/dev/null)"
    [[ -n "$p" ]] && { echo "$p"; return 0; }
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo -e "${RED}未找到可用的 Python 解释器，请先运行：bash deploy/install.sh${NC}"
  exit 1
fi

if ! "$PY" -c 'import botpy' >/dev/null 2>&1; then
  echo -e "${YELLOW}警告：解释器 $PY 里没有 botpy，启动可能失败。${NC}"
  echo -e "${YELLOW}      请运行：$PY -m pip install -r requirements.txt${NC}"
fi

if [[ "$RESTART" -eq 1 ]]; then
  stop_existing
fi

# ---------- 启动 ----------
mkdir -p "$LOG_DIR"

echo -e "${GREEN}启动 QQ 机器人${NC}"
echo "    目录：$ROOT"
echo "    Python：$PY"

if [[ "$DAEMON" -eq 1 ]]; then
  # 后台运行：setsid 脱离当前会话，关掉 SSH 也不会退出
  setsid nohup "$PY" main.py >> "$LOG_FILE" 2>&1 &
  NEW_PID=$!
  echo "$NEW_PID" > "$PID_FILE"
  sleep 2
  if kill -0 "$NEW_PID" 2>/dev/null; then
    echo -e "${GREEN}    已在后台启动，PID=$NEW_PID${NC}"
    echo "    日志：$LOG_FILE"
    echo "    查看日志：tail -f $LOG_FILE"
    echo "    停止：bash deploy/start.sh stop"
  else
    echo -e "${RED}    启动后立即退出，请查看日志：$LOG_FILE${NC}"
    exit 1
  fi
else
  echo "    前台运行中（Ctrl+C 退出）…"
  echo "----------------------------------------"
  exec "$PY" main.py
fi
