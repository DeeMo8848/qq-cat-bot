#!/usr/bin/env bash
# =====================================================================
#  service.sh — 把 bot 注册为 systemd 服务（开机自启 + 崩溃自动重启）
# ---------------------------------------------------------------------
#  用法：
#     sudo bash deploy/service.sh install     # 安装并启动服务
#     sudo bash deploy/service.sh uninstall   # 卸载服务
#     bash deploy/service.sh status           # 查看服务状态
#     bash deploy/service.sh log              # 跟踪日志
#
#  说明：
#     · 与 watchdog.sh 二选一即可。systemd 自带 Restart=always，
#       比 shell 守护更可靠，推荐服务器环境用这个。
#     · 服务名默认 qqbot，可用 SERVICE_NAME 环境变量覆盖。
#     · 想「停掉 bot 就别再拉起」，用 systemctl stop qqbot 即可；
#       要临时只停 bot 保留服务，可 systemctl stop qqbot 后手动改配置。
# =====================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-qqbot}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'

if [[ "$(id -u)" -ne 0 ]]; then
  echo -e "${RED}需要 root 权限，请用：sudo bash deploy/service.sh $*${NC}"
  exit 1
fi

RUN_USER="${SUDO_USER:-root}"

# 挑选解释器（优先有 botpy 的）
pick_python() {
  for c in "$ROOT/.venv/bin/python" python3 python; do
    local p
    if [[ "$c" == /* ]]; then p="$c"; else p="$(command -v "$c" 2>/dev/null)"; fi
    [[ -n "$p" && -x "$p" ]] || continue
    "$p" -c 'import botpy' >/dev/null 2>&1 && { echo "$p"; return 0; }
  done
  for c in python3 python; do
    local p; p="$(command -v "$c" 2>/dev/null)"; [[ -n "$p" ]] && { echo "$p"; return 0; }
  done
  return 1
}

case "${1:-status}" in
  install)
    PY="$(pick_python)" || { echo -e "${RED}找不到 Python 解释器${NC}"; exit 1; }
    echo -e "${GREEN}安装 systemd 服务${NC}"
    echo "    项目目录：$ROOT"
    echo "    Python  ：$PY"
    echo "    运行用户：$RUN_USER"
    echo "    服务名  ：$SERVICE_NAME"

    cat > "$UNIT_PATH" <<EOF
[Unit]
Description=QQ Cat Bot (qq-botpy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$ROOT
ExecStart=$PY $ROOT/main.py
Restart=always
RestartSec=10
# 崩溃频繁时不要无脑重启，10 分钟内最多 5 次
StartLimitIntervalSec=600
StartLimitBurst=5
StandardOutput=append:$ROOT/logs/bot.log
StandardError=append:$ROOT/logs/bot.err.log
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
EOF

    mkdir -p "$ROOT/logs"
    chown -R "$RUN_USER":"$RUN_USER" "$ROOT/logs" 2>/dev/null || true

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    systemctl restart "$SERVICE_NAME"
    sleep 3
    if systemctl is-active --quiet "$SERVICE_NAME"; then
      echo -e "${GREEN}    服务已启动${NC}"
      echo "    查看状态：systemctl status $SERVICE_NAME"
      echo "    查看日志：journalctl -u $SERVICE_NAME -f  或  tail -f $ROOT/logs/bot.log"
      echo "    停止服务：systemctl stop $SERVICE_NAME"
    else
      echo -e "${RED}    服务启动失败，请查看：journalctl -u $SERVICE_NAME -n 50${NC}"
      exit 1
    fi
    ;;

  uninstall)
    echo -e "${YELLOW}卸载服务 $SERVICE_NAME${NC}"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl daemon-reload
    echo -e "${GREEN}    已卸载${NC}"
    ;;

  status)
    systemctl status "$SERVICE_NAME" --no-pager 2>/dev/null || echo "服务未安装"
    ;;

  start|stop|restart)
    systemctl "$1" "$SERVICE_NAME"
    echo -e "${GREEN}    已执行 $1${NC}"
    ;;

  log)
    journalctl -u "$SERVICE_NAME" -f -n 50
    ;;

  *)
    echo "用法：bash deploy/service.sh {install|uninstall|status|start|stop|restart|log}"
    exit 1
    ;;
esac
