@echo off
chcp 65001 >nul
setlocal
echo ==========================================================
echo   切换到：优化前（转火接管 + randomize 已补）
echo ==========================================================
if not exist "%~dp0config.backup-20261101-215955-优化前.json" (
  echo [错误] 找不到 config.backup-20261101-215955-优化前.json
  pause & exit /b 1
)
copy /y "%~dp0config.backup-20261101-215955-优化前.json" "%~dp0config.json" >nul
if exist "C:\Workzone\yolo-trigger-2.0\config.json" (
  copy /y "%~dp0config.backup-20261101-215955-优化前.json" "C:\Workzone\yolo-trigger-2.0\config.json" >nul
  echo 已同步 Workzone 源码目录
)
echo.
echo 新指纹：
certutil -hashfile "%~dp0config.json" MD5 | findstr /r "^[0-9a-f]"
echo.
pause
