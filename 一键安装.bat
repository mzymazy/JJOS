@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   JJOS one-click installer
echo   (Chinese instructions are shown by PowerShell in the next step)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap-install.ps1"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [FAILED] Installation did not complete. See the messages above.
)
endlocal & exit /b %RC%
