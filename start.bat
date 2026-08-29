@echo off
rem Portable launcher for the QQ bot.
rem Uses "python" from PATH first, falls back to "py" (Windows launcher).
rem First run:  powershell -ExecutionPolicy Bypass -File .\install.ps1
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python -u main.py
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -u main.py
    ) else (
        echo Python not found. Install Python 3.10+ first, or edit settings.json PYTHON.
        pause
        exit /b 1
    )
)

echo.
echo Bot exited. Closing this window stops the bot.
pause