@echo off
cd /d "%~dp0"
echo ==========================================
echo   TEST MODE - detection window only
echo   No key will be sent.
echo   Press q inside the window to quit.
echo ==========================================
echo.
python main.py --test %*
if errorlevel 1 pause
