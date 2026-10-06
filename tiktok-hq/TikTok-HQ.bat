@echo off
rem TikTok HQ - Windows launcher
rem   * Double-click            -> opens a file picker
rem   * Drag a video onto it    -> processes that video
rem   * Command line: TikTok-HQ.bat video.mp4 --method all
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where python3 >nul 2>nul && set "PY=python3"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

%PY% "%~dp0tiktok_hq.py" %*
set "RC=%ERRORLEVEL%"
if not "%~1"=="" (
    echo.
    pause
)
exit /b %RC%
