# Запуск демо одной командой: бэкенд, сайт, видео по кругу, настоящий воркер с моделью СИЗ, телефон по USB.
# Двойной клик по scripts\demo.bat или ярлыку «Демо» на рабочем столе. Окно держит демо живым:
# если сервер упал, оно само его перезапускает. Закрыть окно = остановить слежение (демо останется).
# Параметры: -Mock (выдуманные события), -PhoneCamera (камера телефона), -NoPhone, -NoBrowser, -NoWorker.
# Видео по кругу: файлы в %USERPROFILE%\.mosprom\demo-videos, список камер — в autoplay.json там же:
#   {"cam-1": {"video": "склад.mp4", "detect": true}, "video-3": {"video": "x.mp4", "name": "Название"}}
# detect=true: файл читает воркер с моделью (рамки, нарушения); без него — просто видео по кругу.
# Модель СИЗ — %USERPROFILE%\.mosprom\models\best.onnx.
param([switch]$Mock, [switch]$PhoneCamera, [switch]$NoPhone, [switch]$NoBrowser, [switch]$NoWorker)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$url = "http://localhost:5173"
$api = "http://127.0.0.1:8080"
$videos = Join-Path $env:USERPROFILE ".mosprom\demo-videos"
$weights = Join-Path $env:USERPROFILE ".mosprom\models\best.onnx"

function Say($text) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $text) }

function Stop-Demo {
  Get-CimInstance Win32_Process |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match "tasks\.py demo|replay_events\.py|loop_video\.py|worker\.main|uvicorn app\.main|vite(\\bin\\vite\.js| preview)" -and $_.CommandLine -notmatch "upload_big" } |
    ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }
  Start-Sleep -Seconds 2
}

function Start-Demo([switch]$Keep) {
  # воркер с моделью СИЗ запускает сам tasks.py demo --login (если есть веса и ml\.venv)
  $env:MOSPROM_DEMO_WORKER = $(if ($NoWorker) { "0" } else { "1" })
  # перезапуск сторожем не стирает базу посреди показа
  $env:MOSPROM_DEMO_KEEP = $(if ($Keep) { "1" } else { "0" })
  $demoArgs = @("tasks.py", "demo", "--login")
  if (-not $Mock) { $demoArgs += "--no-mock" }
  Start-Process -FilePath "py" -ArgumentList $demoArgs -WorkingDirectory $root -WindowStyle Minimized `
    -RedirectStandardOutput (Join-Path $root "demo.log") -RedirectStandardError (Join-Path $root "demo.err.log") | Out-Null
}

function Test-Ready {
  try {
    $a = (Invoke-WebRequest -Uri "$api/healthz" -UseBasicParsing -TimeoutSec 10).StatusCode
    $b = (Invoke-WebRequest -Uri "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 10).StatusCode
    return ($a -eq 200 -and $b -eq 200)
  } catch { return $false }
}

function Wait-Ready([int]$seconds = 240) {
  for ($i = 0; $i -lt ($seconds / 2); $i++) { if (Test-Ready) { return $true }; Start-Sleep -Seconds 2 }
  return $false
}

# видео из autoplay.json идут по кругу как камеры (вход администратором в демо — без пароля)
function Start-Autoplay {
  $file = Join-Path $videos "autoplay.json"
  if (-not (Test-Path $file)) { return }
  try {
    $map = Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    Invoke-WebRequest -Uri "$api/api/auth/demo" -Method Post -ContentType "application/json" `
      -Body '{"login":"admin"}' -WebSession $session -UseBasicParsing -TimeoutSec 10 | Out-Null
    foreach ($prop in $map.PSObject.Properties) {
      $value = $prop.Value
      $video = if ($value -is [string]) { $value } else { [string]$value.video }
      $payload = @{ camera_id = $prop.Name; video = $video }
      $detect = ($value -isnot [string]) -and $value.detect
      if ($value -isnot [string] -and $value.name) { $payload.name = [string]$value.name }
      if ($detect) { $payload.detect = $true }
      $body = $payload | ConvertTo-Json -Compress
      Invoke-WebRequest -Uri "$api/api/demo/play" -Method Post -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -WebSession $session -UseBasicParsing -TimeoutSec 30 | Out-Null
      Say ("Видео по кругу: {0} -> камера {1}{2}" -f $video, $prop.Name, $(if ($detect) { " (с распознаванием)" } else { "" }))
    }
  } catch { Say "Автозапуск видео не удался: $($_.Exception.Message)" }
}

# настоящий воркер с моделью СИЗ: берёт камеры из бэкенда (файлы с detect, камера телефона)
function Start-Worker {
  if ($NoWorker -or -not (Test-Path $weights)) { return }
  $py = Join-Path $root "ml\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { Say "Нет ml\.venv: воркер пропущен (py tasks.py setup)."; return }
  $env:WORKER_TOKEN = "demo-worker-token"
  Start-Process -FilePath $py -WorkingDirectory (Join-Path $root "ml") -WindowStyle Hidden `
    -ArgumentList @("-m", "worker.main", "--cameras-from-backend", "--loop", "--model", $weights, "--ppe-weights", $weights, "--device", "cpu", "--fps", "4", "--backend", $api) `
    -RedirectStandardOutput (Join-Path $root "worker.log") -RedirectStandardError (Join-Path $root "worker.err.log") | Out-Null
  Say "Воркер с моделью СИЗ запущен (распознавание на процессоре)."
}

function Open-Phone {
  if ($NoPhone) { return }
  $adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
  if (-not $adb) { $adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe" }
  if (-not (Test-Path $adb)) { Say "adb не найден: телефон пропущен."; return }
  $devices = & $adb devices | Select-String "\tdevice$"
  if (-not $devices) { Say "Телефон не найден: включите отладку по USB и подключите кабель (можно без телефона)."; return }
  & $adb reverse tcp:5173 tcp:5173 | Out-Null
  & $adb reverse tcp:8080 tcp:8080 | Out-Null
  $page = if ($PhoneCamera) { "$url/phone-cam.html?auto=1" } else { $url }
  & $adb shell am force-stop com.android.chrome | Out-Null
  & $adb shell "am start -a android.intent.action.VIEW -d '$page'" | Out-Null
  Say "Телефон: открыто $page"
}

# --- запуск ---
Say "Останавливаю прошлый запуск..."
Stop-Demo
Say ("Запускаю демо ({0})..." -f $(if ($Mock) { "с тестовыми событиями" } else { "без тестовых событий" }))
Start-Demo
if (-not (Wait-Ready)) {
  Say "Демо не поднялось за 4 минуты. Смотрите demo.log и demo.err.log в папке проекта."
  Read-Host "Нажмите Enter"
  exit 1
}
Say "Демо запущено: $url"
Start-Autoplay
Open-Phone
if (-not $NoBrowser) { Start-Process $url }

Say ""
Say "Вход: кнопка «Демо-режим — без пароля», затем роль."
Say "Это окно следит за демо и перезапускает его, если оно упадёт. Остановить всё: scripts\demo-stop.bat."

# --- слежение: шесть неудачных проверок подряд (каждая до 10 с, пауза 10 с) — перезапуск ---
$fails = 0
while ($true) {
  Start-Sleep -Seconds 10
  if (Test-Ready) { $fails = 0; continue }
  $fails++
  if ($fails -lt 6) { continue }
  Say "Демо не отвечает: перезапускаю (база сохраняется)..."
  Stop-Demo
  Start-Demo -Keep
  if (Wait-Ready) { Say "Демо снова работает."; Start-Autoplay; Open-Phone } else { Say "Не поднялось, попробую ещё раз через 30 секунд."; Start-Sleep -Seconds 30 }
  $fails = 0
}
