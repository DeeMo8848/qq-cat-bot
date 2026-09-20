#!/usr/bin/env bash
# =====================================================================
#  make_package.sh — 打包成可上传到服务器的部署包
# ---------------------------------------------------------------------
#  生成一个 zip，包含运行所需的全部代码与脚本（不含凭据、运行时数据、
#  外部工具二进制）。上传到服务器解压后，跑 deploy/install.sh 即可。
#
#  用法：
#     bash deploy/make_package.sh              # 输出到 dist/
#     bash deploy/make_package.sh /tmp/out     # 指定输出目录
#
#  产物：dist/qqbot-<日期>.zip
# =====================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

OUT_DIR="${1:-$ROOT/dist}"
mkdir -p "$OUT_DIR"

STAMP="$(date +%Y%m%d)"
NAME="qqbot-${STAMP}"
STAGE="$(mktemp -d)/${NAME}"

GREEN='\033[32m'; YELLOW='\033[33m'; BLUE='\033[36m'; NC='\033[0m'
step() { echo ""; echo -e "${BLUE}>>> $*${NC}"; }
ok()   { echo -e "${GREEN}    ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}    ! $*${NC}"; }

step "准备打包目录"
mkdir -p "$STAGE"
ok "临时目录：$STAGE"

# ---------- 1. 优先用 git archive（只含已跟踪文件，天然排除敏感物）----------
step "导出项目文件"
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE" 2>/dev/null; then
    ok "已用 git archive 导出（仅含已提交文件）"
  else
    warn "git archive 失败，改用文件复制"
    tar --exclude='.git' --exclude='tools' --exclude='resources' \
        --exclude='data' --exclude='logs' --exclude='dist' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='backups' \
        --exclude='tmp' --exclude='settings.json' \
        --exclude='cloudflared*' \
        -cf - -C "$ROOT" . 2>/dev/null | tar -xf - -C "$STAGE"
  fi
else
  warn "不是 git 仓库，改用文件复制"
  tar --exclude='.git' --exclude='tools' --exclude='resources' \
      --exclude='data' --exclude='logs' --exclude='dist' \
      --exclude='__pycache__' --exclude='*.pyc' --exclude='backups' \
      --exclude='tmp' --exclude='settings.json' \
      --exclude='cloudflared*' \
      -cf - -C "$ROOT" . 2>/dev/null | tar -xf - -C "$STAGE"
fi

# ---------- 2. 剔除不该进包的残留 ----------
step "清理不该打包的内容"
REMOVED=0
for pat in ".git" "tools" "resources" "data" "logs" "dist" "backups" "tmp" \
           "settings.json" "__pycache__" ".bot.pid" ".watchdog.pid" "cloudflared" "cloudflared.exe"; do
  if [[ -e "$STAGE/$pat" ]]; then
    rm -rf "$STAGE/$pat" 2>/dev/null && REMOVED=$((REMOVED+1))
  fi
done
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE" -name '*.log' -delete 2>/dev/null || true
ok "已清理 $REMOVED 类项"

# ---------- 3. 安全检查：绝不打包凭据 ----------
step "安全检查"
DANGER=0
for f in "settings.json" "BBDown.data" "BBDownTV.data" ".env"; do
  if find "$STAGE" -name "$f" 2>/dev/null | grep -q .; then
    warn "发现敏感文件：$f（已剔除）"
    find "$STAGE" -name "$f" -delete 2>/dev/null
    DANGER=1
  fi
done
# 扫描是否误带凭据内容
if grep -rlE '(SESSDATA=[A-Za-z0-9%]{20,})|(ghp_[A-Za-z0-9]{20,})|(access_token=[A-Za-z0-9\-_]{20,})' \
     "$STAGE" 2>/dev/null | head -5 | grep -q .; then
  warn "检测到疑似凭据内容，请人工检查上面列出的文件！"
  DANGER=1
fi
if [[ "$DANGER" -eq 0 ]]; then
  ok "未发现凭据/敏感内容"
fi

# ---------- 4. 确保脚本可执行 ----------
step "设置脚本执行权限"
chmod +x "$STAGE/deploy/"*.sh 2>/dev/null || true
ok "deploy/*.sh 已设为可执行"

# ---------- 5. 写部署说明 ----------
step "生成包内说明"
cat > "$STAGE/部署说明.txt" <<'EOF'
QQ 猫猫机器人 — Linux 部署包
====================================================

【上传后三步走】
  1. 解压：  unzip qqbot-*.zip && cd qqbot-*
  2. 装环境：bash deploy/install.sh
  3. 填凭据：nano settings.json   （APPID / SECRET / BOT_ADMINS）
     启动：  bash deploy/start.sh --daemon

【详细文档】
  deploy/README.md

【开机自启 / 崩溃重启（二选一）】
  sudo bash deploy/service.sh install    # systemd（推荐）
  bash deploy/watchdog.sh start          # 独立守护脚本

【远程更新】
  在 QQ 里发「bot更新」或「喵喵更新」（仅管理员/协助者），
  bot 会自动 git pull 并重启。

【注意】
  · 本包不含凭据（settings.json 需自行填写并妥善保管）
  · 本包不含外部工具（BBDown/ffmpeg/cloudflared），
    install.sh 会自动下载；若服务器访问 GitHub 慢，见 deploy/README.md 手动方案
  · 若使用内网穿透（Webhook 模式），需自带 tunnel/config.yml 与证书，
    这些文件不在包内（含凭据）
EOF
ok "已生成 部署说明.txt"

# ---------- 6. 打包 ----------
step "压缩"
OUT="$OUT_DIR/${NAME}.zip"
rm -f "$OUT"
if command -v zip >/dev/null 2>&1; then
  (cd "$(dirname "$STAGE")" && zip -qr "$OUT" "$NAME")
elif command -v 7z >/dev/null 2>&1; then
  (cd "$(dirname "$STAGE")" && 7z a -tzip "$OUT" "$NAME" >/dev/null)
elif command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command \
    "Compress-Archive -Path '$(cygpath -w "$STAGE" 2>/dev/null || echo "$STAGE")' -DestinationPath '$(cygpath -w "$OUT" 2>/dev/null || echo "$OUT")' -Force"
elif command -v tar >/dev/null 2>&1; then
  warn "无 zip，改用 tar.gz"
  OUT="${OUT%.zip}.tar.gz"
  (cd "$(dirname "$STAGE")" && tar -czf "$OUT" "$NAME")
fi

if [[ -f "$OUT" ]]; then
  SIZE="$(du -h "$OUT" 2>/dev/null | cut -f1 || echo '?')"
  echo ""
  echo -e "${GREEN}==============================================${NC}"
  echo -e "${GREEN}打包完成${NC}"
  echo "    文件：$OUT"
  echo "    大小：$SIZE"
  echo ""
  echo "    上传到服务器后："
  echo "      unzip $(basename "$OUT")"
  echo "      cd $NAME"
  echo "      bash deploy/install.sh"
  echo -e "${GREEN}==============================================${NC}"
else
  warn "打包失败"
  exit 1
fi

# 清理临时目录
rm -rf "$(dirname "$STAGE")" 2>/dev/null || true
