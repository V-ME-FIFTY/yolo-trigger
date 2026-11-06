@echo off
chcp 65001 >nul
setlocal
echo ==========================================================
echo   切换到：平衡档（速度接近基准、过冲更小）
echo ==========================================================
if not exist "%~dp0config-平衡档.json" (
  echo [错误] 找不到 config-平衡档.json
  pause & exit /b 1
)
copy /y "%~dp0config-平衡档.json" "%~dp0config.json" >nul
if exist "C:\Workzone\yolo-trigger-2.0\config.json" (
  copy /y "%~dp0config-平衡档.json" "C:\Workzone\yolo-trigger-2.0\config.json" >nul
  echo 已同步 Workzone 源码目录
)
echo.
echo 新指纹：
certutil -hashfile "%~dp0config.json" MD5 | findstr /r "^[0-9a-f]"
echo.
pause
