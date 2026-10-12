@echo off
chcp 65001 >nul
echo Close Voice Bridge via its tray icon before continuing.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_fix.ps1"
set "VB_EXIT=%ERRORLEVEL%"
echo.
if not "%VB_EXIT%"=="0" echo Update failed. See the message and hotfix log above.
pause
exit /b %VB_EXIT%
