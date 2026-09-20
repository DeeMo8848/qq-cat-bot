@echo off
rem ============================================================
rem  run-loop.bat — QQ 机器人「退出自动重启」启动器（Windows）
rem
rem  与 Linux 的 deploy/watchdog.sh 作用相同：让 bot 崩溃或执行了
rem  「bot更新」后能自动回来。区别是本脚本会一直占用一个控制台窗口。
rem
rem  ★ 这是独立程序，不会随 bot 自动开启，需要时手动双击运行。
rem     想彻底停掉 bot：直接关掉本窗口即可（不要只 kill python 进程，
rem     否则会被本循环重新拉起）。
rem
rem  用法：双击运行即可。关闭窗口 = 停止守护并停止 bot。
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

title QQ 猫猫机器人（守护模式）

echo ============================================================
echo   QQ 机器人 守护模式
echo   程序退出会自动重新启动；关闭本窗口即停止。
echo ============================================================
echo.

set /a FAILS=0

:loop
echo [%date% %time%] 启动机器人...
call "%~dp0start.bat"
set "RC=%errorlevel%"

rem 用户在 start.bat 里按 Ctrl+C / 关窗口时，退出码非 0 且通常为 1 或 3221225786
if "%RC%"=="0" (
    echo.
    echo [%date% %time%] 机器人正常退出（执行过更新或正常结束）。
    echo                 3 秒后自动重启...（要停止请关闭本窗口）
    timeout /t 3 /nobreak >nul
    set /a FAILS=0
) else (
    set /a FAILS+=1
    echo.
    echo [%date% %time%] 机器人异常退出（退出码 %RC%，连续失败 %FAILS% 次）
    if !FAILS! GEQ 5 (
        echo.
        echo 连续失败已达 5 次，为免反复刷屏，守护停止。
        echo 请检查上面的报错信息，修好后重新运行本脚本。
        pause
        exit /b 1
    )
    echo                 10 秒后重试...
    timeout /t 10 /nobreak >nul
)
goto loop
