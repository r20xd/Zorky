# Stops the demo (backend, site, looped videos, worker). The LinQ VPN is not touched.
Get-CimInstance Win32_Process |
  Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'tasks\.py demo|replay_events\.py|loop_video\.py|worker\.main|uvicorn app\.main|vite(\\bin\\vite\.js| preview)' -and $_.CommandLine -notmatch 'upload_big' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }
Write-Host 'Demo stopped.'
