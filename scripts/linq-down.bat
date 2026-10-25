@echo off
rem LinQ disconnect: stops OpenVPN started by linq-up. Double click.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0.."
py tasks.py linq-down
echo.
pause
