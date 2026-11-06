@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo ==========================================================
echo   BACK TO BASELINE 2026-10-26  (drop Kalman, restore base)
echo ==========================================================
if exist "main.py" copy /y "main.py" "main.py.bak-before-back-to-base" >nul
if exist "config.json" copy /y "config.json" "config.backup-before-back-to-base.json" >nul
copy /y "C:\yolo-trigger-基准-20261026\main.py" "main.py" >nul
copy /y "C:\yolo-trigger-基准-20261026\config.json" "config.json" >nul
copy /y "C:\yolo-trigger-基准-20261026\pose_trt.py" "pose_trt.py" >nul
for /f %%h in ('certutil -hashfile main.py MD5 ^| findstr /r /c:"^[0-9a-f][0-9a-f]*$"') do echo    main.py = %%h   (base = 7a04237d...)
for /f %%h in ('certutil -hashfile config.json MD5 ^| findstr /r /c:"^[0-9a-f][0-9a-f]*$"') do echo    config  = %%h   (base = f268640f...)
pause
