@echo off
rem Stops the demo (backend, web, event generator). The LinQ VPN is not touched.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo-stop.ps1"
pause
