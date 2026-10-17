# One-click live run on Town04, everything on this PC: CarlaAir, the backend stack (docker, backend, dashboard, live pipeline),
# highway violation traffic (speeding E1, lane stops B1, shoulder stops B2), the drone stream and the gamepad / keyboard controls.
# The drone is NOT moved anywhere: fly it yourself (fly_drone.py opens in its own window; A = take off).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_town04_live.ps1 [-Session town04_live_1] [-Stoppers 2] [-ShoulderStoppers 2] [-People off|auto|on]
#   (or double-click scripts\start_town04_live.bat)
#
# Each step is skipped when its part already runs (CarlaAir on :2000, Town04 already loaded, backend :8000, dashboard :5173,
# pipeline :5555, and the scenario / stream / controls processes), so it is safe to run again after something stopped.
# CarlaAir.ps1 kills a running CarlaAir before it starts one: that is why step 1 is skipped when :2000 is already up.
# Logs: ml\data\results\live\<session>_*.log. Stop: scripts\stop_town04_live.ps1.
param(
    [string]$Session = ("town04_live_" + (Get-Date -Format "MMdd_HHmm")),
    [int]$Stoppers = 2,                # B1: cars that stop on a highway lane for 40 s, then drive on (repeats)
    [int]$ShoulderStoppers = 2,        # B2: the same on the shoulder (engine status: possible_breakdown)
    [string]$Quality = "Medium",       # Medium: CARLA and the detector share the GPU (2026-10-15 run)
    [int]$Imgsz = 960,
    [ValidateSet("auto", "on", "off")][string]$People = "off",   # pedestrian detector: off for the highway demo (2026-10-16), auto = near crosswalks
    [string]$SimPython = (Join-Path $env:USERPROFILE ".conda\envs\carlaAir\python.exe")   # carla + airsim live only in this env
)

$ErrorActionPreference = "Continue"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo
$carlaAir = Join-Path $repo "CarlaAir-v0.1.7-Windows11-x86_64\CarlaAir.ps1"
$logDir = Join-Path $repo "ml\data\results\live"
New-Item -ItemType Directory -Force $logDir | Out-Null
function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }
function Running($pattern) { [bool](Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match $pattern }) }
function Step($n, $text) { Write-Host "`n== $n. $text ==" -ForegroundColor Cyan }
function StartSim($name, $argList) {
    # a carlaAir-env script in the background; stdout / stderr to <session>_<name>.log / .err
    Start-Process -FilePath $SimPython -ArgumentList (@("-u") + $argList) -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "${Session}_$name.log") -RedirectStandardError (Join-Path $logDir "${Session}_$name.err") | Out-Null
}

if (-not (Test-Path $SimPython)) { Write-Host "carlaAir python not found: $SimPython (give -SimPython)" -ForegroundColor Red; exit 1 }

Step 1 "CarlaAir (:2000)"
if (Listening 2000) { Write-Host "already running" }
else {
    # blocks until CARLA and AirSim answer (up to ~6 min), then returns with CarlaAir running on its own
    & powershell -NoProfile -ExecutionPolicy Bypass -File $carlaAir --quality $Quality --no-traffic
    if (-not (Listening 2000)) { Write-Host "CarlaAir did not come up: see CarlaAir-v0.1.7-Windows11-x86_64\CarlaAir.log" -ForegroundColor Red; exit 1 }
}

Step 2 "Town04"
# the packaged build boots Town10HD whatever map it is given (load_map.py docstring); a reload resets the world, so only when needed
$out = & $SimPython -c "import carla; c = carla.Client('localhost', 2000); c.set_timeout(20); print('MAP=' + c.get_world().get_map().name)" 2>$null
$map = ($out | Where-Object { $_ -match "^MAP=" } | Select-Object -First 1) -replace "^MAP=", ""   # carla also prints INFO lines
if ($map -match "Town04$") { Write-Host "already loaded" }
elseif (Running "highway_violations\.py|stream_flight\.py|fly_drone\.py") {
    Write-Host "map is '$map' but scenario / stream / controls are running: stop them (stop_town04_live.ps1) before loading Town04" -ForegroundColor Red; exit 1
}
else {
    Write-Host "map is '$map': loading Town04 (and waiting for AirSim to resync)..."
    & $SimPython simulation\carla_scripts\load_map.py Town04
    if ($LASTEXITCODE -ne 0) { Write-Host "load_map.py failed" -ForegroundColor Red; exit 1 }
}

Step 3 "Backend stack: docker, backend, dashboard, live pipeline"
$pipeWasUp = Listening 5555
& (Join-Path $repo "scripts\live_test\03_start_stack.ps1") -Session $Session -Imgsz $Imgsz -People $People -Local
if (-not (Listening 5555)) { Write-Host "the live pipeline is not listening on :5555: stopping here" -ForegroundColor Red; exit 1 }
if ($pipeWasUp) {
    # a pipeline from an earlier start keeps its own session: report that one (the newest heartbeat), not the new name
    $hb = Get-ChildItem $logDir -Directory -ErrorAction SilentlyContinue | ForEach-Object { Get-Item (Join-Path $_.FullName "heartbeat.json") -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($hb) { $Session = $hb.Directory.Name; Write-Host "(using the running pipeline's session: $Session)" }
}

Step 4 "Violation traffic (highway_violations.py: speeding, lane stops, shoulder stops)"
if (Running "highway_violations\.py") { Write-Host "already running" }
else {
    StartSim "traffic" @("simulation\violation_scenarios\highway_violations.py", "--stoppers", "$Stoppers", "--shoulder-stoppers", "$ShoulderStoppers", "--duration", "0")
    Write-Host "started ($Stoppers lane stoppers, $ShoulderStoppers shoulder stoppers; cars run on the east-side northbound straight, x ~ -6..-17, y -218..207)"
}

Step 5 "Drone stream to the pipeline"
if (Running "stream_flight\.py") { Write-Host "already running (it keeps its own session name)" }
else {
    StartSim "stream" @("simulation\carla_scripts\stream_flight.py", "--to", "127.0.0.1", "--session", $Session)
    Write-Host "started"
}

Step 6 "Drone controls (gamepad + keyboard)"
if (Running "fly_drone\.py") { Write-Host "already running" }
else {
    # its own console window: the key listener and the gamepad status are shown there; Ctrl+C there leaves the drone hovering
    Start-Process -FilePath $SimPython -ArgumentList @("-u", "simulation\carla_scripts\fly_drone.py") -WorkingDirectory $repo -WindowStyle Normal | Out-Null
    Write-Host "opened in a new window"
}

Write-Host "`n== Ready ==" -ForegroundColor Green
Write-Host "Session   : $Session"
Write-Host "Dashboard : http://localhost:5173  (planner / planner123)  ->  Live monitoring, session $Session"
Write-Host "Fly       : A = take off, left stick move, right stick yaw, RT up, LT down (B = land)"
Write-Host "            the highway traffic runs on Town04's east side, around x -10, y -218..207"
Write-Host "Logs      : $logDir\${Session}_*.log"
Write-Host "Stop      : powershell -ExecutionPolicy Bypass -File scripts\stop_town04_live.ps1 [-All] [-Carla]"
