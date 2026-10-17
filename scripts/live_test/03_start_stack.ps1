# Two-machine live test, step 3 (detector PC): start everything that runs here, in the right order, and say what to tell Afif.
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\03_start_stack.ps1 [-Session live_test_1] [-Zones <scenario_log.json>] [-ClockOffset 0.0] [-Dashboard]
#
# Starts (skipping what already runs): docker services (PostGIS, Redis, S3), the backend (:8000), the dashboard (:5173) and the live
# pipeline under its watchdog (:5555, waiting for Afif's frames). Logs: ml\data\results\live\<session>_*.log. Stop: 04_stop_stack.ps1.
param(
    [string]$Session = ("live_test_" + (Get-Date -Format "MMdd_HHmm")),
    [string]$Zones = "",              # scenario_log.json of a staged flight (zones + expected violations) when testing staged acts
    [double]$ClockOffset = 0.0,       # from net_check.py ping (seconds)
    [switch]$Dashboard,               # also reachable from Afif's PC (backend / dashboard listen on all interfaces)
    [switch]$Local,                   # everything on this PC (start_town04_live.ps1): no "tell Afif" summary at the end
    [int]$Imgsz = 960,
    [ValidateSet("auto", "on", "off")][string]$People = "auto"   # pedestrian detector (zebra rules): auto = only near a crosswalk
)

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repo
$py = Join-Path $repo "venv\Scripts\python.exe"
$logDir = Join-Path $repo "ml\data\results\live"
New-Item -ItemType Directory -Force $logDir | Out-Null
function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }
function Wait-Port($port, $secs) { for ($i = 0; $i -lt $secs; $i++) { if (Listening $port) { return $true }; Start-Sleep -Seconds 1 }; return $false }

Write-Host "== 1. Docker services ==" -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running: starting Docker Desktop (this can take a minute)..."
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) { docker info *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep -Seconds 5 }
}
$ErrorActionPreference = "Continue"
docker compose up -d *> $null   # docker prints its progress on stderr, which PowerShell shows as errors: hide it, show the result
docker compose ps --format "  {{.Name}}: {{.Status}}"

Write-Host "`n== 2. Backend (:8000) ==" -ForegroundColor Cyan
if (Listening 8000) { Write-Host "already running (restart it yourself if you changed backend code since it started)" }
else {
    $bind = if ($Dashboard) { "0.0.0.0" } else { "127.0.0.1" }
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "backend.app.main:app", "--host", $bind, "--port", "8000" -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "${Session}_backend.log") -RedirectStandardError (Join-Path $logDir "${Session}_backend.err")
    if (Wait-Port 8000 40) { Write-Host "up" -ForegroundColor Green } else { Write-Host "did not start: see ${Session}_backend.err" -ForegroundColor Red; exit 1 }
}
try { $h = Invoke-RestMethod http://127.0.0.1:8000/api/health; Write-Host "health: db=$($h.db) redis=$($h.redis) s3=$($h.s3)" } catch { Write-Host "health check failed: $_" -ForegroundColor Red }

Write-Host "`n== 3. Dashboard (:5173) ==" -ForegroundColor Cyan
if (Listening 5173) { Write-Host "already running" }
else {
    $fe = Join-Path $repo "frontend"
    $feArgs = if ($Dashboard) { "run dev -- --host 0.0.0.0" } else { "run dev" }
    Start-Process -FilePath "npm.cmd" -ArgumentList $feArgs -WorkingDirectory $fe -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "${Session}_dashboard.log") -RedirectStandardError (Join-Path $logDir "${Session}_dashboard.err")
    if (Wait-Port 5173 40) { Write-Host "up" -ForegroundColor Green } else { Write-Host "did not start: see ${Session}_dashboard.err" -ForegroundColor Red }
}

Write-Host "`n== 4. Live pipeline under the watchdog (:5555) ==" -ForegroundColor Cyan
if (Listening 5555) { Write-Host "something already listens on 5555 (a pipeline from an earlier run?). Run 04_stop_stack.ps1 first." -ForegroundColor Yellow }
else {
    $a = @("services\live\watchdog.py", "--backend", "http://127.0.0.1:8000", "--session", $Session, "--imgsz", "$Imgsz", "--tracker", "tracktrack_live", "--people", $People)
    if ($Zones) { $a += @("--zones", (Resolve-Path $Zones).Path) }
    if ($ClockOffset -ne 0.0) { $a += @("--clock-offset", "$ClockOffset") }
    Start-Process -FilePath $py -ArgumentList $a -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "${Session}_pipeline.log") -RedirectStandardError (Join-Path $logDir "${Session}_pipeline.err")
    Write-Host "loading the detector (about 20-40 s on the RTX 4050)..."
    if (Wait-Port 5555 120) { Write-Host "pipeline is listening on :5555 and waiting for frames" -ForegroundColor Green }
    else { Write-Host "pipeline did not open :5555: see ${Session}_pipeline.err" -ForegroundColor Red; exit 1 }
}

if ($Local) { return }
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue)
$ip = if ($ts) { (& tailscale ip -4 2>$null | Select-Object -First 1) } else { $null }
if (-not $ip) { $ip = "<this PC's Tailscale IP>" }
Write-Host "`n== Ready ==" -ForegroundColor Green
Write-Host "Session name : $Session"
Write-Host "Dashboard    : http://localhost:5173   (login planner / planner123, operator / operator123, ...)"
Write-Host "Backend docs : http://localhost:8000/docs"
Write-Host "Logs         : $logDir\${Session}_*.log"
Write-Host "`nTell Afif to run on his PC (in the CarlaAir conda env), after CarlaAir is up and the drone is at the spot:"
Write-Host "    python simulation\carla_scripts\stream_flight.py --to $ip --session $Session" -ForegroundColor Yellow
Write-Host "Then open 'Live monitoring' in the dashboard, pick session $Session."
