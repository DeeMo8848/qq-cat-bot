#!/usr/bin/env bash
# =====================================================================
#  install.sh — QQ 机器人 Linux 一键安装 / 环境准备脚本
# ---------------------------------------------------------------------
#  对应 Windows 版 install.ps1，职责一致：
#    1. 检查 Python（3.10+，缺则尝试用 apt/yum 安装）
#    2. 安装 requirements.txt + meme-generator==0.1.14（固定版本，勿升 rs 版）
#    3. 下载 BBDown（linux-x64）
#    4. 下载 ffmpeg（静态构建）
#    5. 下载 cloudflared（linux amd64/arm64）
#    6. 克隆 cardforge（卡牌制作工具）
#    7. 生成 settings.json（需手动填凭据）
#    8. 克隆图库仓库 / 拉取 meme 素材
#    9. 重建 meme 关键词数据
#
#  用法：
#     bash deploy/install.sh
#     bash deploy/install.sh --token ghp_xxx     # 拉私有资源仓库需令牌
#     bash deploy/install.sh --skip-tools        # 跳过 exe 下载（已自备）
# =====================================================================
set -uo pipefail

# ---------- 基础 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

GIT_TOKEN=""
SKIP_TOOLS=0
PY=""

BLUE='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'
step()  { echo ""; echo -e "${BLUE}>>> $*${NC}"; }
ok()    { echo -e "${GREEN}    ✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}    ! $*${NC}"; }
err()   { echo -e "${RED}    ✗ $*${NC}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) GIT_TOKEN="$2"; shift 2 ;;
    --skip-tools) SKIP_TOOLS=1; shift ;;
    --python) PY="$2"; shift 2 ;;
    *) warn "未知参数：$1"; shift ;;
  esac
done

echo -e "${GREEN}QQ 机器人 Linux 安装程序${NC}"
echo "    项目目录：$ROOT"
echo "    系统架构：$(uname -m)"

# 判断是否有 sudo 可用
SUDO=""
if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

# ---------- 1. Python ----------
step "第 1 步 / 共 9 步：Python 环境检测"
find_python() {
  for c in "$PY" python3.12 python3.11 python3.10 python3 python; do
    [[ -z "$c" ]] && continue
    if command -v "$c" >/dev/null 2>&1; then
      v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
      if [[ -n "$v" ]]; then
        major="${v%%.*}"; minor="${v##*.}"
        if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
          echo "$c"; return 0
        fi
      fi
    fi
  done
  return 1
}

if ! PY="$(find_python)"; then
  warn "未找到 Python 3.10+，尝试自动安装…"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y python3 python3-pip python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip
  fi
  if ! PY="$(find_python)"; then
    err "仍找不到 Python 3.10+，请手动安装后重跑本脚本。"
    exit 1
  fi
fi
PY="$(command -v "$PY")"
ok "使用 Python：$PY ($("$PY" --version 2>&1))"

# 确保 pip / venv 可用
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  warn "pip 不可用，尝试安装 python3-pip…"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get install -y python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3-pip
  fi
fi

# ---------- 2. pip 依赖 ----------
step "第 2 步 / 共 9 步：安装 pip 依赖"
"$PY" -m pip install --upgrade pip -q 2>/dev/null
if "$PY" -m pip install -r requirements.txt -q; then
  ok "requirements.txt 安装完成"
else
  warn "requirements 安装有报错，继续尝试关键依赖…"
fi
# meme-generator 固定 0.1.14：0.2.x 为 Rust 重写版，会导致生成功能失效，切勿升级
if "$PY" -m pip install "meme-generator==0.1.14" -q; then
  ok "meme-generator 0.1.14 就绪"
else
  err "meme-generator 安装失败，请检查网络或换源后重试。"
  exit 1
fi

# ---------- 3~5. 外部工具 ----------
TOOLS="$ROOT/tools"
mkdir -p "$TOOLS"

download() {  # download <url> <dest>
  local url="$1" dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 20 -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 -O "$dest" "$url"
  else
    err "系统没有 curl 或 wget，无法下载。请先安装：apt install -y curl"
    return 1
  fi
}

# 检测架构：BBDown/cloudflared 的发行包命名不同
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) CFD_ARCH="amd64"; BB_ARCH="x64" ;;
  aarch64|arm64) CFD_ARCH="arm64"; BB_ARCH="arm64" ;;
  *) CFD_ARCH="amd64"; BB_ARCH="x64"; warn "未知架构 $ARCH，按 x64 尝试" ;;
esac

step "第 3 步 / 共 9 步：下载 BBDown"
BB_DIR="$TOOLS/BBDown"; BB_BIN="$BB_DIR/BBDown"
if [[ "$SKIP_TOOLS" -eq 1 ]]; then
  warn "已指定 --skip-tools，跳过"
elif [[ -x "$BB_BIN" ]]; then
  ok "BBDown 已存在，跳过"
else
  mkdir -p "$BB_DIR"
  BB_VER="1.6.3"; BB_TAG="1.6.3_20240814"
  BB_URL="https://github.com/nilaoda/BBDown/releases/download/${BB_VER}/BBDown_${BB_TAG}_linux-${BB_ARCH}.zip"
  TMPD="$(mktemp -d)"
  echo "    下载：$BB_URL"
  if download "$BB_URL" "$TMPD/BBDown.zip"; then
    if command -v unzip >/dev/null 2>&1; then
      unzip -oq "$TMPD/BBDown.zip" -d "$TMPD"
    else
      $SUDO apt-get install -y unzip >/dev/null 2>&1 || true
      unzip -oq "$TMPD/BBDown.zip" -d "$TMPD"
    fi
    FOUND="$(find "$TMPD" -name 'BBDown*' -type f ! -name '*.zip' | head -1)"
    if [[ -n "$FOUND" ]]; then
      cp -f "$FOUND" "$BB_BIN"; chmod +x "$BB_BIN"
      ok "BBDown 就绪：$BB_BIN"
    else
      warn "解压后未找到 BBDown，请手动下载放到 $BB_DIR"
    fi
  else
    warn "BBDown 下载失败（网络问题？），可稍后手动下载到 $BB_DIR"
  fi
  rm -rf "$TMPD"
fi

step "第 4 步 / 共 9 步：准备 ffmpeg"
FF_DIR="$TOOLS/ffmpeg"; FF_BIN="$FF_DIR/ffmpeg"
if [[ "$SKIP_TOOLS" -eq 1 ]]; then
  warn "已指定 --skip-tools，跳过"
elif [[ -x "$FF_BIN" ]]; then
  ok "ffmpeg 已存在，跳过"
elif command -v ffmpeg >/dev/null 2>&1; then
  ok "系统已有 ffmpeg：$(command -v ffmpeg)"
else
  mkdir -p "$FF_DIR"; TMPD="$(mktemp -d)"
  FF_URL="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
  echo "    下载：$FF_URL"
  if download "$FF_URL" "$TMPD/ffmpeg.tar.xz"; then
    tar -xJf "$TMPD/ffmpeg.tar.xz" -C "$TMPD" 2>/dev/null || tar -xf "$TMPD/ffmpeg.tar.xz" -C "$TMPD"
    FOUND="$(find "$TMPD" -name 'ffmpeg' -type f | head -1)"
    if [[ -n "$FOUND" ]]; then
      cp -f "$FOUND" "$FF_BIN"; chmod +x "$FF_BIN"
      ok "ffmpeg 就绪：$FF_BIN"
    else
      warn "解压后未找到 ffmpeg，将回退使用系统 ffmpeg"
    fi
  else
    warn "ffmpeg 下载失败，尝试用包管理器安装…"
    if command -v apt-get >/dev/null 2>&1; then
      $SUDO apt-get install -y ffmpeg && ok "系统 ffmpeg 安装完成"
    fi
  fi
  rm -rf "$TMPD"
fi

step "第 5 步 / 共 9 步：下载 cloudflared（内网穿透）"
CFD_BIN="$ROOT/cloudflared"
if [[ "$SKIP_TOOLS" -eq 1 ]]; then
  warn "已指定 --skip-tools，跳过"
elif [[ -x "$CFD_BIN" ]]; then
  ok "cloudflared 已存在，跳过"
else
  CFD_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CFD_ARCH}"
  echo "    下载：$CFD_URL"
  if download "$CFD_URL" "$CFD_BIN"; then
    chmod +x "$CFD_BIN"
    ok "cloudflared 就绪：$CFD_BIN"
  else
    warn "cloudflared 下载失败。若不需要内网穿透（Webhook 模式）可忽略；"
    warn "否则请手动下载到 $CFD_BIN 并 chmod +x"
  fi
fi

# ---------- 6. cardforge ----------
step "第 6 步 / 共 9 步：克隆 cardforge（卡牌制作工具）"
CF_DIR="$TOOLS/cardforge"
if [[ -f "$CF_DIR/cardforge.py" ]]; then
  ok "cardforge 已存在，跳过"
elif ! command -v git >/dev/null 2>&1; then
  warn "未安装 git，跳过（可在 settings.json 的 CARD_DIR 指定已有目录）"
else
  CF_URL="https://github.com/DeeMo8848/cardforge.git"
  [[ -n "$GIT_TOKEN" ]] && CF_URL="https://x-access-token:${GIT_TOKEN}@github.com/DeeMo8848/cardforge.git"
  if git clone --depth 1 "$CF_URL" "$CF_DIR" >/dev/null 2>&1; then
    ok "cardforge 就绪（首次做卡自动装依赖）"
  else
    warn "cardforge 克隆失败（私有仓库需 --token）"
  fi
fi

# ---------- 7. 配置 ----------
step "第 7 步 / 共 9 步：初始化 settings.json"
if [[ ! -f "$ROOT/settings.json" ]]; then
  cp "$ROOT/settings.example.json" "$ROOT/settings.json"
  warn "已生成 settings.json，请填写：APPID / SECRET / BOT_ADMINS"
else
  ok "settings.json 已存在，跳过"
fi

# ---------- 8. 资源 ----------
step "第 8 步 / 共 9 步：拉取图库与 meme 素材"
if command -v git >/dev/null 2>&1; then
  IMG_LIB="$ROOT/resources/image_lib"
  mkdir -p "$ROOT/resources"
  if [[ -d "$IMG_LIB/dragon" ]]; then
    ok "图库已存在，跳过"
  else
    IMG_URL="https://github.com/DeeMo8848/qq-cat-image-lib.git"
    [[ -n "$GIT_TOKEN" ]] && IMG_URL="https://x-access-token:${GIT_TOKEN}@github.com/DeeMo8848/qq-cat-image-lib.git"
    if git clone --depth 1 "$IMG_URL" "$IMG_LIB" >/dev/null 2>&1; then
      ok "图库就绪"
    else
      warn "图库克隆失败（私有仓库需 --token），可在 settings.json 指定 DRAGON_DIR"
    fi
  fi

  CUSTOM="$ROOT/bot/meme/custom_memes"
  mkdir -p "$CUSTOM"
  SRC="$CUSTOM/_sources"
  AGG_OK=0
  if [[ -n "$GIT_TOKEN" && ! -d "$SRC/meme_emoji" ]]; then
    if git clone --recursive --depth 1 \
        "https://x-access-token:${GIT_TOKEN}@github.com/DeeMo8848/qq-cat-memes.git" "$SRC" >/dev/null 2>&1; then
      AGG_OK=1; rm -rf "$SRC/.git"
    fi
  fi
  if [[ "$AGG_OK" -eq 0 ]]; then
    for url in \
      "https://github.com/anyliew/meme_emoji" \
      "https://github.com/MemeCrafters/meme-generator-contrib" \
      "https://github.com/anyliew/crazy_emoji" \
      "https://github.com/USYDShawnTan/meme-demo" ; do
      name="${url##*/}"; dst="$SRC/$name"
      [[ -d "$dst" ]] && continue
      echo "    拉取扩展仓库：$url"
      git clone --depth 1 "$url" "$dst" >/dev/null 2>&1 || warn "拉取失败：$name"
    done
  fi
  # 把各源的 memes/* 模板装载进 custom_memes
  if [[ -d "$SRC" ]]; then
    for d in "$SRC"/*/; do
      [[ -d "$d" ]] || continue
      meme_dir="$d/memes"
      [[ -d "$meme_dir" ]] || meme_dir="$d"
      for m in "$meme_dir"/*/; do
        [[ -f "$m/__init__.py" ]] || continue
        tgt="$CUSTOM/$(basename "$m")"
        [[ -d "$tgt" ]] || cp -r "$m" "$tgt"
      done
    done
    ok "meme 素材装载完成"
  fi
else
  warn "未安装 git，跳过资源拉取"
fi

# ---------- 9. 重建 meme 关键词 ----------
step "第 9 步 / 共 9 步：重建 meme 关键词数据"
if "$PY" bot/meme/rebuild_data.py >/dev/null 2>&1; then
  ok "meme 关键词已重建"
else
  warn "重建失败（仓库自带一份 meme_data.py，可正常用；稍后用「meme更新」重试）"
fi

# ---------- 完成 ----------
echo ""
echo -e "${GREEN}==============================================${NC}"
echo -e "${GREEN}环境准备完成！${NC}"
echo "  1. 编辑 settings.json 填入 APPID / SECRET / BOT_ADMINS"
echo "  2. 启动机器人：bash deploy/start.sh"
echo "  3. 开机自启：bash deploy/service.sh install   （注册 systemd 服务）"
echo "  4. 进程守护：bash deploy/watchdog.sh start    （可选，崩了自动拉起）"
echo -e "${GREEN}==============================================${NC}"
