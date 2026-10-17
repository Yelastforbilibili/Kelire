@echo off
setlocal
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "TianTianAnime" pythonw.exe "%~dp0run.py"
    exit /b 0
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    start "TianTianAnime" python.exe "%~dp0run.py"
    exit /b 0
)

echo Python 3.10+ was not found. Install Python first, then run this file again.
pause
