@echo off
setlocal
title Transport Autopilot
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0install.py" %*
    goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0install.py" %*
    goto finished
)
echo Python 3.8 or later is required.
echo Install Python 3 from https://www.python.org/downloads/ and run this installer again.
cmd /c exit 1
:finished
set "TA_EXIT=%ERRORLEVEL%"
if not "%TA_NO_PAUSE%"=="1" pause
exit /b %TA_EXIT%
