# =====================================================================
#  install.ps1  —  QQ 机器人首次安装 / 备份恢复环境准备脚本
# ---------------------------------------------------------------------
#  用途：检测本机是否具备运行环境，缺失的依赖自动下载到项目内并安装。
#   1. 检测 Python，安装 requirements.txt + meme-generator==0.1.14（固定版本，勿升 rs 版）
#   2. 下载 BBDown 1.6.3 到 tools/BBDown/
#   3. 下载 ffmpeg 到 tools/ffmpeg/
#   4. 生成 settings.json（从 settings.example.json 复制，需手动填入凭据）
#   5. 可选：从公开扩展仓库拉取额外 meme 到 bot/meme/custom_memes/
#
#  用法（在本目录执行）：
#     powershell -ExecutionPolicy Bypass -File .\install.ps1
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 -PythonPath "C:\Python310\python.exe"
# =====================================================================
param([string]$PythonPath = "python")

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
Write-Step "第 1 步 / 共 5 步：Python 环境检测"
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
Write-Step "第 2 步 / 共 5 步：安装 pip 依赖"
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

Write-Step "第 3 步 / 共 5 步：下载 BBDown"
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

Write-Step "第 3 步 / 共 5 步：下载 ffmpeg"
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

# ---------- 4. 配置 ----------
Write-Step "第 4 步 / 共 5 步：初始化配置 settings.json"
$settingFp = Join-Path $Root "settings.json"
if (-not (Test-Path $settingFp)) {
    Copy-Item (Join-Path $Root "settings.example.json") $settingFp
    Write-Host "已生成 settings.json，请打开并填入：机器人 AppID / Secret / 管理员 openid / 本地龙图目录等。" -ForegroundColor Yellow
} else {
    Write-Host "settings.json 已存在，跳过。"
}

# ---------- 5. 扩展 meme（可选）----------
Write-Step "第 5 步 / 共 5 步：拉取扩展 meme（可选）"
$custom = Join-Path $Root "bot\meme\custom_memes"
New-Item -ItemType Directory -Force -Path $custom | Out-Null
if (-not (Test-Cmd git)) {
    Write-Host "未安装 git，跳过扩展 meme 拉取（内置 meme 来自 meme-generator 包，已经可正常使用）。" -ForegroundColor Yellow
} else {
    $repos = @(
        "https://github.com/anyliew/meme_emoji",
        "https://github.com/MemeCrafters/meme-generator-contrib",
        "https://github.com/anyliew/crazy_emoji",
        "https://github.com/USYDShawnTan/meme-demo"
    )
    $src = Join-Path $custom "_sources"
    New-Item -ItemType Directory -Force -Path $src | Out-Null
    foreach ($url in $repos) {
        $name = ($url -Split "/")[-1]
        $dst = Join-Path $src $name
        if (-not (Test-Path $dst)) {
            Write-Host "拉取扩展仓库: $url"
            git clone --depth 1 $url $dst 2>&1 | Out-Null
        }
        $memeDir = Join-Path $dst "memes"
        if (-not (Test-Path $memeDir)) { $memeDir = $dst }
        if (Test-Path $memeDir) {
            Get-ChildItem $memeDir -Directory | ForEach-Object {
                if (Test-Path (Join-Path $_.FullName "__init__.py")) {
                    $target = Join-Path $custom $_.Name
                    if (-not (Test-Path $target)) {
                        Copy-Item $_.FullName $target -Recurse -Force
                    }
                }
            }
        }
    }
    Write-Host "扩展 meme 拉取完成（与内置重复的关键词会被自动忽略）。"
}

# 重建 meme 关键词静态数据，确保与当前安装的模板集一致（含项目内 custom_memes）
Write-Step "第 5 步 / 共 5 步：重建 meme 关键词数据"
& $Py bot\meme\rebuild_data.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "meme 关键词重建失败（仓库已带一份 meme_data.py，可正常使用；稍后用「meme更新」重试）。" -ForegroundColor Yellow
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