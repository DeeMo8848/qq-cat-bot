# =====================================================================
#  install.ps1  —  QQ 机器人首次安装 / 备份恢复环境准备脚本
# ---------------------------------------------------------------------
#  用途：检测本机是否具备运行环境，缺失的依赖自动下载到项目内并安装。
#   1. 检测 Python，安装 requirements.txt + meme-generator==0.1.14（固定版本，勿升 rs 版）
#   2. 下载 BBDown 1.6.3 到 tools/BBDown/
#   3. 下载 ffmpeg 到 tools/ffmpeg/
#   4. 下载 cloudflared 到项目根（内网穿透）
#   5. 克隆 cardforge（卡牌制作工具）到 tools/cardforge/
#   6. 生成 settings.json（从 settings.example.json 复制，需手动填入凭据）
#   7. 克隆「图库仓库」到 resources/image_lib（龙图目录自动使用其 dragon/ 子目录）
#   8. 拉取 meme 素材：优先克隆聚合仓库 qq-cat-memes（含子模块），失败则直接克隆公开源仓库
#
#  用法（在本目录执行）：
#     powershell -ExecutionPolicy Bypass -File .\install.ps1
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 -PythonPath "C:\Python310\python.exe"
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 -GitToken "ghp_xxx"   # 仅当资源仓库改为私有时才需要
# =====================================================================
param(
    [string]$PythonPath = "python",
    [string]$GitToken = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step($msg) {
    Write-Host ""
    Write-Host ">>> $msg" -ForegroundColor Cyan
}
function Test-Cmd($name) { (Get-Command $name -ErrorAction SilentlyContinue) -ne $null }

Write-Host "QQ 机器人环境安装程序" -ForegroundColor Green
Write-Host "项目目录: $Root"

# ---------- 1. Python ----------
Write-Step "第 1 步 / 共 8 步：Python 环境检测"
if (Test-Path $PythonPath) { $Py = $PythonPath }
elseif (Test-Cmd $PythonPath) { $Py = (Get-Command $PythonPath).Source }
else {
    Write-Host "未找到 Python（$PythonPath）。请先安装 Python 3.10+，或用 -PythonPath 指定解释器路径。" -ForegroundColor Yellow
    exit 1
}
Write-Host "使用 Python: $Py"
& $Py --version
if ($LASTEXITCODE -ne 0) { exit 1 }

# ---------- 2. pip 依赖 ----------
Write-Step "第 2 步 / 共 8 步：安装 pip 依赖"
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "requirements 安装失败，继续尝试安装 meme-generator..." -ForegroundColor Yellow
}
# meme-generator 固定 0.1.14：0.2.x 为 Rust 重写版，会导致生成功能失效，切勿升级
& $Py -m pip install "meme-generator==0.1.14"
if ($LASTEXITCODE -ne 0) {
    Write-Host "meme-generator 安装失败，请检查网络。" -ForegroundColor Red
    exit 1
}

# ---------- 3. 工具下载 ----------
$tools = Join-Path $Root "tools"
New-Item -ItemType Directory -Force -Path $tools | Out-Null

Write-Step "第 3 步 / 共 8 步：下载 BBDown"
$bbDir = Join-Path $tools "BBDown"
$bbExe = Join-Path $bbDir "BBDown.exe"
if (Test-Path $bbExe) {
    Write-Host "BBDown 已存在，跳过。"
} else {
    Write-Host "下载 BBDown 1.6.3 ..."
    $tmp = Join-Path $env:TEMP "qqbot_bbdown"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "BBDown.zip"
    Invoke-WebRequest "https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_win-x64.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $bbDir | Out-Null
    $found = Get-ChildItem $tmp -Recurse -Filter "BBDown.exe" | Select-Object -First 1
    if (-not $found) { Write-Host "BBDown 解压后未找到 BBDown.exe，请手动下载放到 $bbDir" -ForegroundColor Red; exit 1 }
    Copy-Item $found.FullName $bbExe -Force
    Write-Host "BBDown 就绪: $bbExe"
}

Write-Step "第 3 步 / 共 8 步：下载 ffmpeg"
$ffDir = Join-Path $tools "ffmpeg"
$ffExe = Join-Path $ffDir "ffmpeg.exe"
if (Test-Path $ffExe) {
    Write-Host "ffmpeg 已存在，跳过。"
} else {
    Write-Host "下载 ffmpeg（gyan.dev essentials）..."
    $tmp = Join-Path $env:TEMP "qqbot_ffmpeg"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "ffmpeg.zip"
    try {
        Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    } catch {
        Write-Host "gyan.dev 下载失败，改用 BtbN/FFmpeg-Builds ..." -ForegroundColor Yellow
        Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-n7.1-latest-win64-gpl-7.1.zip" -OutFile $zip
    }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
    $found = Get-ChildItem $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $found) { Write-Host "ffmpeg 解压后未找到 ffmpeg.exe，请手动下载放到 $ffDir" -ForegroundColor Red; exit 1 }
    Copy-Item $found.FullName $ffExe -Force
    Write-Host "ffmpeg 就绪: $ffExe"
}

Write-Step "第 3 步 / 共 8 步：下载 cloudflared（内网穿透）"
$cfdBin = Join-Path $Root "cloudflared.exe"
if (Test-Path $cfdBin) {
    Write-Host "cloudflared 已存在，跳过。"
} else {
    Write-Host "下载 cloudflared ..."
    try {
        Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cfdBin
        Write-Host "cloudflared 就绪: $cfdBin"
    } catch {
        Write-Host "cloudflared 下载失败。若不需要内网穿透（Webhook 回调）可忽略；" -ForegroundColor Yellow
        Write-Host "否则请手动下载 cloudflared-windows-amd64.exe 放到项目根并改名为 cloudflared.exe" -ForegroundColor Yellow
    }
}

# ---------- 4. cardforge（卡牌制作工具）----------
Write-Step "第 4 步 / 共 8 步：克隆 cardforge（卡牌制作工具）"
$cfDir = Join-Path $tools "cardforge"
if (Test-Path (Join-Path $cfDir "cardforge.py")) {
    Write-Host "cardforge 已存在（$cfDir），跳过。"
} elseif (-not (Test-Cmd git)) {
    Write-Host "未安装 git，跳过 cardforge 拉取（可在 settings.json 的 CARD_DIR 指定已有目录）。" -ForegroundColor Yellow
} else {
    Write-Host "克隆 cardforge ..."
    $cfUrl = "https://github.com/DeeMo8848/cardforge.git"
    if ($GitToken) { $cfUrl = "https://x-access-token:$GitToken@github.com/DeeMo8848/cardforge.git" }
    git clone --depth 1 $cfUrl $cfDir 2>&1 | Out-Null
    if (Test-Path (Join-Path $cfDir "cardforge.py")) {
        Write-Host "cardforge 就绪: $cfDir"
    } else {
        Write-Host "cardforge 克隆失败。可在 settings.json 的 CARD_DIR 指定已有目录。" -ForegroundColor Yellow
    }
}

# ---------- 4b. 项目级共享 venv ----------
# ★ 共享 venv 放在 tools\.venv，由所有「需要独立依赖的工具」复用（目前是 cardforge）。
#   位置固定、不入库，见 bot/core/venv.py。cardforge 通过 CARDFORGE_VENV 环境变量
#   或它自己 settings.json 的 venv 字段指向这里，不必在自身目录再建一份。
Write-Step "第 4 步(附) / 共 8 步：准备项目级共享 venv 与 cardforge 依赖"
$sharedVenv = Join-Path $tools ".venv"
$sharedPy   = Join-Path $sharedVenv "Scripts\python.exe"
$cfReq      = Join-Path $cfDir "requirements.txt"

if (-not (Test-Path (Join-Path $cfDir "cardforge.py"))) {
    Write-Host "cardforge 不存在，跳过共享 venv 准备。" -ForegroundColor Yellow
} else {
    if (-not (Test-Path $sharedPy)) {
        Write-Host "创建共享 venv: $sharedVenv"
        & $Py -m venv $sharedVenv
    }
    if (-not (Test-Path $sharedPy)) {
        Write-Host "创建共享 venv 失败，卡牌制作将不可用（其余功能不受影响）。" -ForegroundColor Yellow
    } else {
        Write-Host "共享 venv 就绪: $sharedVenv"
        # 依赖较大（rembg + onnxruntime），已装过则跳过
        $cfDepsOk = $false
        if (Test-Path $cfReq) {
            & $sharedPy -c "import rembg, onnxruntime" 2>$null
            if ($LASTEXITCODE -eq 0) { $cfDepsOk = $true }
        }
        if ($cfDepsOk) {
            Write-Host "cardforge 依赖已就绪，跳过。"
        } elseif (Test-Path $cfReq) {
            Write-Host "安装 cardforge 依赖（rembg / onnxruntime，体积较大，请耐心等待）..."
            & $sharedPy -m pip install --upgrade pip -q 2>$null
            & $sharedPy -m pip install -r $cfReq -q
            if ($LASTEXITCODE -eq 0) {
                Write-Host "cardforge 依赖安装完成。"
            } else {
                Write-Host "cardforge 依赖安装失败，卡牌制作暂不可用。" -ForegroundColor Yellow
                Write-Host "可稍后手动重试: $sharedPy -m pip install -r $cfReq" -ForegroundColor Yellow
            }
        }
        # 把 venv 位置写回 cardforge 的 settings.json，便于它被独立调用时也走同一环境
        # （路径含中文/空格，用临时 py 文件而非 here-string 传参，避免引号转义问题）
        $fixVenvPy = Join-Path $env:TEMP "cf_fix_venv_$PID.py"
        @"
import json, os, sys
root, venv = sys.argv[1], sys.argv[2]
path = os.path.join(root, "settings.json")
cfg = {}
if os.path.isfile(path):
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception:
        cfg = {}
if cfg.get("venv") != venv:
    cfg["venv"] = venv
    try:
        json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass
"@ | Set-Content -Path $fixVenvPy -Encoding UTF8
        & $sharedPy $fixVenvPy $cfDir $sharedVenv 2>$null
        Remove-Item $fixVenvPy -Force -ErrorAction SilentlyContinue

        # 素材完整性检查
        $bgDir = Join-Path $cfDir "assets\backgrounds"
        $frDir = Join-Path $cfDir "assets\frames"
        $nBg = 0; $nFr = 0
        if (Test-Path $bgDir) { $nBg = (Get-ChildItem $bgDir -File -ErrorAction SilentlyContinue).Count }
        if (Test-Path $frDir) { $nFr = (Get-ChildItem $frDir -File -ErrorAction SilentlyContinue).Count }
        if ($nBg -lt 5 -or $nFr -lt 3) {
            Write-Host "cardforge 素材似乎不完整（背景 $nBg / 边框 $nFr）。" -ForegroundColor Yellow
            Write-Host "若素材在独立仓库，请手动补齐到 $cfDir\assets\" -ForegroundColor Yellow
        } else {
            Write-Host "cardforge 素材就绪（背景 $nBg / 边框 $nFr）。"
        }

        # ★ 内存体检：本地抠图模型吃内存，小内存机器硬跑会被 OOM 拖死整机
        #   （2026-09-21 在 1.6GB ECS 上跑 BiRefNet-lite 导致机器完全无响应）。
        #   cardforge 侧有运行时守卫（engine.py 的 _mem_guard 自动降级），
        #   这里部署期再提醒一次。
        try {
            $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
            $memMb = [int]($os.FreePhysicalMemory / 1024)
            if ($memMb -lt 1200) {
                Write-Host "本机可用内存仅 ${memMb}MB —— 本地抠图会自动降级到小模型或改为不抠图。" -ForegroundColor Yellow
                Write-Host "若需高质量抠图，请在 cardforge\settings.json 配置 matting_api（阿里云分割抠图）。" -ForegroundColor Yellow
            } elseif ($memMb -lt 2500) {
                Write-Host "本机可用内存 ${memMb}MB —— 本地抠图可用，但建议避开超大图或改用抠图 API。" -ForegroundColor Yellow
            } else {
                Write-Host "内存充足（可用 ${memMb}MB），本地抠图可正常运行。"
            }
        } catch {
            # 取不到内存信息就静默跳过，不影响安装
        }
    }
}

# ---------- 5. 配置 ----------
Write-Step "第 5 步 / 共 8 步：初始化配置 settings.json"
$settingFp = Join-Path $Root "settings.json"
if (-not (Test-Path $settingFp)) {
    Copy-Item (Join-Path $Root "settings.example.json") $settingFp
    Write-Host "已生成 settings.json，请打开并填入：机器人 AppID / Secret / 管理员 openid（龙图目录无需填，会自动从图库仓库拉取）。" -ForegroundColor Yellow
} else {
    Write-Host "settings.json 已存在，跳过。"
}

# ---------- 6. 图库（龙图）----------
Write-Step "第 6 步 / 共 8 步：克隆图库仓库（龙图素材）"
$imgLib = Join-Path $Root "resources\image_lib"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "resources") | Out-Null
if (Test-Path (Join-Path $imgLib "dragon")) {
    Write-Host "图库已存在（$imgLib），跳过。"
} elseif (-not (Test-Cmd git)) {
    Write-Host "未安装 git，跳过图库拉取（可在 settings.json 的 DRAGON_DIR 手动指定龙图目录）。" -ForegroundColor Yellow
} else {
    Write-Host "克隆图库仓库 ..."
    $imgUrl = "https://github.com/DeeMo8848/qq-cat-image-lib.git"
    if ($GitToken) { $imgUrl = "https://x-access-token:$GitToken@github.com/DeeMo8848/qq-cat-image-lib.git" }
    git clone --depth 1 $imgUrl $imgLib 2>&1 | Out-Null
    if (Test-Path (Join-Path $imgLib "dragon")) {
        Write-Host "图库就绪，龙图目录: $(Join-Path $imgLib 'dragon')"
    } else {
        Write-Host "图库克隆失败，可在 settings.json 的 DRAGON_DIR 手动指定。" -ForegroundColor Yellow
    }
}

# ---------- 7. meme 素材 ----------
Write-Step "第 7 步 / 共 8 步：拉取 meme 素材"
$custom = Join-Path $Root "bot\meme\custom_memes"
New-Item -ItemType Directory -Force -Path $custom | Out-Null
if (-not (Test-Cmd git)) {
    Write-Host "未安装 git，跳过扩展 meme 拉取（内置 meme 来自 meme-generator 包，已经可正常使用）。" -ForegroundColor Yellow
} else {
    $src = Join-Path $custom "_sources"
    # 三个资源仓库（qq-cat-memes / qq-cat-image-lib / cardforge）实测均为【公开仓库】，
    # 无需令牌即可克隆。$GitToken 只在被改成私有仓库时才需要。
    if (-not (Test-Path (Join-Path $src "meme_emoji"))) {
        Write-Host "拉取聚合仓库 qq-cat-memes（含子模块）..."
        $aggUrl = "https://github.com/DeeMo8848/qq-cat-memes.git"
        if ($GitToken) { $aggUrl = "https://x-access-token:$GitToken@github.com/DeeMo8848/qq-cat-memes.git" }
        git clone --recursive --depth 1 $aggUrl $src 2>&1 | Out-Null
        if (Test-Path (Join-Path $src "meme_emoji")) {
            Get-ChildItem $src -Recurse -Force -Directory -Filter ".git" -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "聚合仓库克隆失败，回退为直接克隆 4 个公开源仓库" -ForegroundColor Yellow
        }
    }
    if (-not (Test-Path (Join-Path $src "meme_emoji"))) {
        # 回退：直接克隆 4 个公开源仓库（无需令牌）
        $repos = @(
            "https://github.com/anyliew/meme_emoji",
            "https://github.com/MemeCrafters/meme-generator-contrib",
            "https://github.com/anyliew/crazy_emoji",
            "https://github.com/USYDShawnTan/meme-demo"
        )
        foreach ($url in $repos) {
            $name = ($url -Split "/")[-1]
            $dst = Join-Path $src $name
            if (-not (Test-Path $dst)) {
                Write-Host "拉取扩展仓库: $url"
                git clone --depth 1 $url $dst 2>&1 | Out-Null
                Remove-Item (Join-Path $dst ".git") -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    # 把各源的模板装载进 custom_memes。
    # ★ 统一走 meme_sources.py：按内容自适应识别模板容器，
    #   兼容 meme_emoji/crazy_emoji 的 emoji/ 布局与其余仓库的 memes/ 布局。
    #   （早先的 PowerShell 版只找 memes/，会把 emoji/ 布局的两个仓库静默漏掉 445 个模板。）
    if (Test-Path $src) {
        # 用真实字符写一个临时 py 脚本再执行 —— 比 here-string 稳妥，
        # 也不受 PowerShell 对 @" / 引号转义的挑剔影响。
        $loaderPath = Join-Path $env:TEMP "qqbot_load_sources.py"
        $lines = @(
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from bot.meme.meme_sources import sync_sources",
            "c, s, k, t = sync_sources(custom_dir=sys.argv[2], sources_dir=sys.argv[3], verbose=False)",
            "print('%d %d %d %d' % (c, s, k, t))"
        )
        Set-Content -Path $loaderPath -Value $lines -Encoding UTF8
        $res = & $Py $loaderPath $Root $custom $src 2>$null | Select-Object -Last 1
        if ($res) {
            $parts = $res -split '\s+'
            if ($parts.Count -ge 4 -and [int]$parts[3] -gt 0) {
                Write-Host "meme 素材装载完成：源仓库 $($parts[3]) 个模板（新复制/覆盖 $($parts[0])，已最新 $($parts[1])，本地独有保留 $($parts[2])）"
            } else {
                Write-Host "meme 素材装载数量为 0 —— 请检查 $src 是否为空" -ForegroundColor Yellow
            }
        } else {
            Write-Host "meme 素材装载脚本执行失败，可手动运行：python bot\meme\meme_sources.py" -ForegroundColor Yellow
        }
    }
}

# ---------- 8. 重建 meme 关键词 ----------
Write-Step "第 8 步 / 共 8 步：重建 meme 关键词数据"
& $Py bot\meme\rebuild_meme_data.py --whitelist cache\meme_list_kw.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "新脚本重建失败，回退旧脚本..." -ForegroundColor Yellow
    & $Py bot\meme\rebuild_data.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "meme 关键词重建失败（仓库已带一份 meme_data.py，可正常使用；稍后用「meme更新」重试）。" -ForegroundColor Yellow
    }
} else {
    Write-Host "meme 关键词已重建。"
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "环境准备完成！" -ForegroundColor Green
Write-Host "  · 复制 settings.example.json 为 settings.json 并填入凭据（已自动生成则直接编辑）"
Write-Host "  · 用 start.bat 或 python main.py 启动机器人"
Write-Host "  · 若使用自定义 Python 作为子进程解释器，请同步 settings.json 的 PYTHON 字段"
Write-Host "=============================================="