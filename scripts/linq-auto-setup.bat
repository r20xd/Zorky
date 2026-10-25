@echo off
rem One-time setup: after it, linq-up and linq-down run without UAC prompts.
rem Creates two elevated Task Scheduler tasks (manual start only).
rem Undo: schtasks /delete /tn mosprom-linq-up /f  and  schtasks /delete /tn mosprom-linq-down /f
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights, confirm the Windows prompt once...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set "RUNNER=%~dp0..\linq\run_task.cmd"
schtasks /create /tn "mosprom-linq-up" /tr "\"%RUNNER%\" linq-up" /sc once /st 00:00 /rl highest /f
schtasks /create /tn "mosprom-linq-down" /tr "\"%RUNNER%\" linq-down" /sc once /st 00:00 /rl highest /f
echo.
echo Done. linq-up and linq-down now run without prompts.
pause
