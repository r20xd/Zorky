@echo off
rem Demo launcher: double click. Optional flags: -Mock (fake events), -PhoneCamera, -NoPhone, -NoBrowser
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo.ps1" %*
