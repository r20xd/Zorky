@echo off
rem Run by an elevated Task Scheduler task: run_task.cmd linq-up | linq-down
rem Output goes to linq_up.log in the repo root; the last line __DONE__ marks the end.
rem After linq-up the same elevated window stays as the tunnel watcher (linq_watch.log):
rem it re-raises the VPN if the stand stops answering. linq-down stops it.
cd /d "%~dp0.."
py tasks.py %1 > linq_up.log 2>&1
echo __DONE__>> linq_up.log
if /i "%1"=="linq-up" py tasks.py linq-watch > linq_watch.log 2>&1
