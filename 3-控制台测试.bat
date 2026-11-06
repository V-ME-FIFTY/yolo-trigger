@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   CONSOLE TEST - no detection window
echo   Just watch this console:
echo     [LOCK] ...  = target found near crosshair
echo     (no key is sent in this mode)
echo   Ctrl+C = quit
echo ============================================
echo.
python main.py --test --no-window %*
pause
