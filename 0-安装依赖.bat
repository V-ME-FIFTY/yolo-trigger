@echo off
cd /d "%~dp0"
echo ==========================================
echo   Install dependencies - first run only
echo   torch + ultralytics + opencv + dxcam
echo   about 2.5 GB, may take 5-30 min
echo ==========================================
echo.
python -m pip install --upgrade ultralytics opencv-python dxcam
echo.
echo Done. Next step: run 1-test.bat
pause
