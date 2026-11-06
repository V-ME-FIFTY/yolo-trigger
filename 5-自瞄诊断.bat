@echo off
cd /d "%~dp0"
echo ==========================================================
echo   AIM DIAGNOSTIC (console only, no detection window)
echo ==========================================================
echo   In game:
echo     1) Look at an enemy (you do NOT need to hold RMB)
echo     2) Watch the lines starting with [AIM]/[aim]
echo   Then copy the last 15 lines back to the assistant.
echo ----------------------------------------------------------
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/k','python','main.py','--no-window' -WorkingDirectory '%~dp0' -Verb RunAs"