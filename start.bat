@echo off
rem ============================================================
rem  QQ 猫猫机器人启动脚本（便携版）
rem
rem  会按优先级挑选一个"能 import botpy"的 Python：
rem     1) PATH 上的每个 python（逐个探测 botpy）
rem     2) Windows 的 py 启动器（取其真实解释器路径，并再探测 botpy）
rem     3) 本机已知的 TRAE 运行时（开发机兜底）
rem
rem  首次部署前先运行:  install.ps1
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem --- 本机已知能运行 bot 的 Python（TRAE 运行时；可自行增删）---
set "KNOWN_DEV_PY=C:\Users\DeeMo\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"

set "PY="

rem 1) 扫描 PATH 上所有 python，取第一个带 botpy 的
for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
        "%%P" -c "import botpy" >nul 2>nul && set "PY=%%P"
    )
)

rem 2) Windows py 启动器：先解析真实解释器路径，再确认它带 botpy 才采用
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
            "%%P" -c "import botpy" >nul 2>nul && set "PY=%%P"
        )
    )
)

rem 3) 本机 TRAE 运行时兜底
if not defined PY (
    if exist "%KNOWN_DEV_PY%" (
        "%KNOWN_DEV_PY%" -c "import botpy" >nul 2>nul && set "PY=%KNOWN_DEV_PY%"
    )
)

if not defined PY (
    echo [start.bat] 未找到装有 botpy 的 Python.
    echo 请先运行 install.ps1 安装依赖；或把正确的解释器路径填进 settings.json 的 "PYTHON"。
    pause
    exit /b 1
)

echo 使用 Python: %PY%
"%PY%" -u main.py

echo.
echo 机器人已退出。关闭本窗口即停止 bot。
pause
