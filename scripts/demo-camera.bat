@echo off
rem Demo launcher with the phone used as a camera (phone page opens and starts the camera).
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1" -PhoneCamera %*
