@echo off
call "%~dp0install.bat" uninstall %*
exit /b %ERRORLEVEL%
