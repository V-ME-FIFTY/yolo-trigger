@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo ==========================================================
echo   SHISHAN without preview window (elevated, with UAC)
echo ==========================================================
echo   F9 master  F6 aim  F5 mouse path
echo ==========================================================
echo.
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/k','set PYTHONUTF8=1& python -u main.py --no-window' -WorkingDirectory '%~dp0' -Verb RunAs"
echo.
echo   started in the elevated window; keep it open to read the log.
timeout /t 4 >nul
