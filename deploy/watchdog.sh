#!/usr/bin/env bash
# =====================================================================
#  watchdog.sh — QQ 机器人进程守护（独立程序，手动启停）
# ---------------------------------------------------------------------
#  作用：定时检查 bot 是否存活，发现死掉就自动拉起。
#
#  ★ 重要：本脚本不会随 bot 一起启动，必须手动开启。
#     这样你主动关掉 bot 时，不会立刻被它重新拉起来。
#
#  用法：
#     bash deploy/watchdog.sh start      # 开始守护（后台）
#     bash deploy/watchdog.sh stop       # 停止守护（并停止被守护的 bot）
#     bash deploy/watchdog.sh status     # 查看状态
#     bash deploy/watchdog.sh restart    # 重启守护
#     bash deploy/watchdog.sh log        # 查看守护日志
#     bash deploy/watchdog.sh run        # 前台运行（调试用，Ctrl+C 退出）
#
#  可选环境变量：
#     INTERVAL=30      检查间隔（秒），默认 30
#     MAX_FAILS=3      连续失败多少次才重启（防抖动），默认 3
#     START_CMD="..."  自定义启动命令，默认用 deploy/start.sh --daemon
# =====================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

INTERVAL="${INTERVAL:-30}"
MAX_FAILS="${MAX_FAILS:-3}"
WATCH_PID_FILE="$ROOT/.watchdog.pid"
BOT_PID_FILE="$ROOT/.bot.pid"
LOG_DIR="$ROOT/logs"
WATCH_LOG="$LOG_DIR/watchdog.log"

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'

mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$WATCH_LOG"
}

# ---------- 判断 bot 是否存活 ----------
# 列出疑似 bot 主进程的 PID（排除本守护脚本自身）
_find_bot_pids() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "python.*main\.py" 2>/dev/null | grep -v "^$$\$" || true
    return
  fi
  # 无 pgrep 时的兜底：扫 /proc（Linux 一定有）
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

bot_alive() {
  # 优先用 PID 文件
  if [[ -f "$BOT_PID_FILE" ]]; then
    local pid; pid="$(cat "$BOT_PID_FILE" 2>/dev/null)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      # 确认确实是我们的 main.py，而非 PID 被复用
      if [[ -r "/proc/$pid/cmdline" ]] \
         && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'main.py'; then
        return 0
      fi
    fi
  fi
  # 兜底：按命令行匹配
  local found
  found="$(_find_bot_pids)"
  [[ -n "$found" ]]
}

# ---------- 启动 bot ----------
start_bot() {
  log "检测到 bot 未运行，正在拉起…"
  # 用 --daemon 方式启动，PID 会写进 .bot.pid
  if bash "$SCRIPT_DIR/start.sh" --daemon >> "$WATCH_LOG" 2>&1; then
    sleep 3
    if bot_alive; then
      log "✅ bot 已拉起"
      return 0
    fi
  fi
  log "❌ 拉起失败，下一轮重试"
  return 1
}

# ---------- 守护主循环 ----------
watch_loop() {
  log "======== 守护启动（间隔 ${INTERVAL}s，连续失败 ${MAX_FAILS} 次才重启）========"
  local fails=0
  while true; do
    if bot_alive; then
      if [[ "$fails" -gt 0 ]]; then
        log "bot 恢复正常（此前连续失败 $fails 次）"
      fi
      fails=0
    else
      fails=$((fails + 1))
      log "⚠️  第 $fails/$MAX_FAILS 次检测到 bot 未运行"
      if [[ "$fails" -ge "$MAX_FAILS" ]]; then
        start_bot
        fails=0
      fi
    fi
    sleep "$INTERVAL"
  done
}

# ---------- 停止 ----------
do_stop() {
  # 先停守护，避免它又把 bot 拉起来
  if [[ -f "$WATCH_PID_FILE" ]]; then
    local wpid; wpid="$(cat "$WATCH_PID_FILE" 2>/dev/null)"
    if [[ -n "$wpid" ]] && kill -0 "$wpid" 2>/dev/null; then
      log "停止守护进程 PID=$wpid"
      kill "$wpid" 2>/dev/null
      for _ in $(seq 1 10); do
        kill -0 "$wpid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$wpid" 2>/dev/null && kill -9 "$wpid" 2>/dev/null
    fi
    rm -f "$WATCH_PID_FILE"
  fi
  # 再停 bot
  bash "$SCRIPT_DIR/start.sh" stop >/dev/null 2>&1 || true
  echo -e "${GREEN}守护已停止，bot 也已停止${NC}"
}

# ---------- 命令分发 ----------
CMD="${1:-status}"
case "$CMD" in
  start)
    if [[ -f "$WATCH_PID_FILE" ]] && kill -0 "$(cat "$WATCH_PID_FILE" 2>/dev/null)" 2>/dev/null; then
      echo -e "${YELLOW}守护已在运行（PID=$(cat "$WATCH_PID_FILE")）${NC}"
      exit 0
    fi
    # 脱离当前会话启动。优先 setsid（Linux 标准），无则用 nohup 兜底，
    # 两者都没有就不要假装启动成功。
    if command -v setsid >/dev/null 2>&1; then
      setsid nohup bash "$0" run >> "$WATCH_LOG" 2>&1 &
    elif command -v nohup >/dev/null 2>&1; then
      nohup bash "$0" run >> "$WATCH_LOG" 2>&1 &
    else
      bash "$0" run >> "$WATCH_LOG" 2>&1 &
    fi
    sleep 1.5
    # 必须复查进程真的活着，否则明确报错（避免"谎报启动成功"）
    if [[ ! -f "$WATCH_PID_FILE" ]]; then
      echo -e "${RED}守护启动失败：未生成 PID 文件${NC}"
      echo "    请查看日志：$WATCH_LOG"
      exit 1
    fi
    WPID="$(cat "$WATCH_PID_FILE" 2>/dev/null)"
    if [[ -z "$WPID" ]] || ! kill -0 "$WPID" 2>/dev/null; then
      echo -e "${RED}守护启动失败：进程未存活${NC}"
      echo "    请查看日志：$WATCH_LOG"
      exit 1
    fi
    echo -e "${GREEN}守护已启动${NC}"
    echo "    PID    : $WPID"
    echo "    间隔   : ${INTERVAL}s"
    echo "    日志   : $WATCH_LOG"
    echo "    停止   : bash deploy/watchdog.sh stop"
    echo ""
    echo -e "${YELLOW}    注意：bot 若当前没在运行，守护会在约 $((INTERVAL * MAX_FAILS)) 秒后自动拉起它。${NC}"
    ;;

  stop)
    do_stop
    ;;

  status)
    echo "======== 守护状态 ========"
    if [[ -f "$WATCH_PID_FILE" ]] && kill -0 "$(cat "$WATCH_PID_FILE" 2>/dev/null)" 2>/dev/null; then
      echo -e "守护进程：${GREEN}运行中${NC} (PID=$(cat "$WATCH_PID_FILE"))"
    else
      echo -e "守护进程：${YELLOW}未运行${NC}"
    fi
    if bot_alive; then
      bot_pid="$(cat "$BOT_PID_FILE" 2>/dev/null || echo '?')"
      echo -e "机器人  ：${GREEN}运行中${NC} (PID=$bot_pid)"
    else
      echo -e "机器人  ：${RED}未运行${NC}"
    fi
    echo "检查间隔：${INTERVAL}s"
    echo "守护日志：$WATCH_LOG"
    ;;

  restart)
    do_stop
    sleep 1
    exec bash "$0" start
    ;;

  log)
    if [[ -f "$WATCH_LOG" ]]; then
      tail -n 50 "$WATCH_LOG"
    else
      echo "暂无日志"
    fi
    ;;

  run)
    # 前台运行（由 start 通过 setsid 调起）
    echo $$ > "$WATCH_PID_FILE"
    trap 'log "守护收到退出信号，停止"; rm -f "$WATCH_PID_FILE"; exit 0' TERM INT
    watch_loop
    ;;

  *)
    echo "用法：bash deploy/watchdog.sh {start|stop|status|restart|log|run}"
    exit 1
    ;;
esac
