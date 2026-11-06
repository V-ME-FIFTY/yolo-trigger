@echo off
chcp 65001 >nul
setlocal
echo ==========================================================
echo   回退：转火接管改造（回到 2026-11-01 21:48:57 之前）
echo   影响：main.py + config.json（运行目录 + Workzone 源码目录）
echo ==========================================================
echo.

if not exist "%~dp0main.py.bak-20261101-214857-转火接管" (
  echo [错误] 找不到备份 main.py.bak-20261101-214857-转火接管
  pause & exit /b 1
)
if not exist "%~dp0config.backup-20261101-214857.json" (
  echo [错误] 找不到备份 config.backup-20261101-214857.json
  pause & exit /b 1
)

copy /y "%~dp0main.py.bak-20261101-214857-转火接管" "%~dp0main.py" >nul
copy /y "%~dp0config.backup-20261101-214857.json" "%~dp0config.json" >nul
if exist "C:\Workzone\yolo-trigger-2.0\main.py.bak-20261101-214857-转火接管" (
  copy /y "C:\Workzone\yolo-trigger-2.0\main.py.bak-20261101-214857-转火接管" "C:\Workzone\yolo-trigger-2.0\main.py" >nul
  copy /y "C:\Workzone\yolo-trigger-2.0\config.backup-20261101-214857.json" "C:\Workzone\yolo-trigger-2.0\config.json" >nul
  echo 已同步回退 Workzone 源码目录
)

echo.
echo 回退后指纹（应为 main.py B73C61FC0D6B02FF177D63FA0AB9F65E / config 2E959496B55AACBF2DCA777E56E6B78C）：
certutil -hashfile "%~dp0main.py" MD5 | findstr /r "^[0-9a-f]"
certutil -hashfile "%~dp0config.json" MD5 | findstr /r "^[0-9a-f]"
echo.
echo 完成。已恢复：never_switch=true / 迟滞 0.3 / 冷却 1000ms / 无 commit 窗口。
pause