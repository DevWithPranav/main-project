# Two-machine live test, last step (detector PC): stop the live pipeline cleanly (and, with -All, the backend and dashboard).
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\04_stop_stack.ps1 [-Session <name>] [-All] [-Docker]
# The pipeline is asked to finish by a STOP file (it then writes summary.json, events.jsonl, kinematics.csv); it is only killed if it
# has not exited after 40 s. The session defaults to the one that wrote a heartbeat most recently.
param([string]$Session = "", [switch]$All, [switch]$Docker)

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$live = Join-Path $repo "ml\data\results\live"
function Matching($pattern) { Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern -and $_.ProcessId -ne $PID } }

if (-not $Session) {
    $hb = Get-ChildItem $live -Directory -ErrorAction SilentlyContinue | ForEach-Object { Get-Item (Join-Path $_.FullName "heartbeat.json") -ErrorAction SilentlyContinue } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($hb) { $Session = $hb.Directory.Name }
}
$pipes = Matching "services.live.pipeline\.py"
if ($pipes -and $Session) {
    $sd = Join-Path $live $Session
    New-Item -ItemType File -Force (Join-Path $sd "STOP") | Out-Null
    Write-Host "pipeline : asked session '$Session' to finish (STOP file), waiting up to 40 s..."
    for ($i = 0; $i -lt 40; $i++) { if (-not (Matching "services.live.pipeline\.py")) { break }; Start-Sleep -Seconds 1 }
}
foreach ($label in @(@("services.live.watchdog\.py", "watchdog"), @("services.live.pipeline\.py", "pipeline (killed: did not finish in time)"), @("services.live.net_check\.py", "net_check"))) {
    $procs = Matching $label[0]
    if (-not $procs) { Write-Host ("{0,-10}: not running" -f $label[1].Split(" ")[0]); continue }
    foreach ($p in $procs) { Write-Host "$($label[1]) : stopping pid $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
if ($All) {
    foreach ($p in (Matching "uvicorn.*backend\.app\.main")) { Write-Host "backend  : stopping pid $($p.ProcessId)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    $c = Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { Write-Host "dashboard: stopping pid $($c.OwningProcess)"; Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
}
if ($Docker) { docker compose stop *> $null; Write-Host "docker   : stopped" }
if ($Session) {
    $sum = Join-Path $live "$Session\summary.json"
    if (Test-Path $sum) {
        $j = Get-Content $sum -Raw | ConvertFrom-Json
        Write-Host "`nSession $Session : frames received $($j.frames_received), processed $($j.frames_processed) ($($j.fps_processed) fps), dropped latest-wins $($j.frames_dropped_latest_wins), events $($j.events_final), backend posts ok $($j.backend_posts_ok) / failed $($j.backend_posts_failed)"
        Write-Host "  full numbers: $sum"
    } else { Write-Host "`nno summary.json for $Session (the pipeline was killed, or no frames arrived)" }
}
