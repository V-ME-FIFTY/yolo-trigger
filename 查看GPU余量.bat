@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
echo === 自瞄能不能挤进 GPU（游戏保持在前台） ===
echo.
python _check_gpu.py 6
echo.
echo 顺便看一眼自瞄当前那档的参数：
python -c "import json;c=json.load(open('config.json',encoding='utf-8'));a=c['aim'];print('  region',c['region_size'],'| engine',c['pose_engine'],'| detect_every',c.get('detect_every',1),'| max_step',a['max_step_px'])"
echo.
pause
