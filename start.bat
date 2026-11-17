@echo off
rem RechKontrol launcher. Options: -Llm -NoDocker -NoBrowser -Stop -Port 8081
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set RK_EXIT=%ERRORLEVEL%
if not "%RK_EXIT%"=="0" pause
exit /b %RK_EXIT%
