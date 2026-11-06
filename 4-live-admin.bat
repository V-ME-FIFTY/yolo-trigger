@echo off
cd /d "%~dp0"
echo YOLO trigger - LIVE with administrator rights
echo A UAC prompt will appear, click YES.
powershell -NoProfile -Command "Start-Process -FilePath cmd -ArgumentList '/k','python','main.py' -WorkingDirectory '%~dp0' -Verb RunAs"
