@echo off
rem LinQ connect: double click. Asks for administrator rights itself (needed by OpenVPN).
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights, confirm the Windows prompt...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0.."
py tasks.py linq-up
echo.
pause
