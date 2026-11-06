@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if exist "start-log.txt" del /q "start-log.txt"
echo ==========================================================
echo   SHISHAN  (basis + Kalman tracker, aim only, no fire)
echo ==========================================================
echo   F9 master  F6 aim  F5 mouse path  q quit preview
echo   log: start-log.txt   (window never closes by itself)
echo ==========================================================
echo.
python -u main.py %* 2>&1 | powershell -NoProfile -Command "$input | ForEach-Object { $_; Add-Content -LiteralPath 'start-log.txt' -Value $_ -Encoding utf8 }"
echo.
echo ----------------------------------------------------------
echo   ==== last 20 lines (also in start-log.txt) ====
powershell -NoProfile -Command "if (Test-Path 'start-log.txt') { Get-Content 'start-log.txt' -Encoding utf8 -Tail 20 }"
echo ----------------------------------------------------------
pause
