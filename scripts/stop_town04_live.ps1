# Stop what start_town04_live.ps1 started.
#   powershell -ExecutionPolicy Bypass -File scripts\stop_town04_live.ps1            # scenario, stream, controls, live pipeline
#   ... -All                                                                          # + backend and dashboard
#   ... -All -Carla                                                                   # + CarlaAir
# The pipeline finishes cleanly (04_stop_stack.ps1: STOP file, summary.json written). The scenario is ended hard, so its cars
# stay in the world where they are (a restart of highway_violations.py destroys nothing; reload Town04 or restart CarlaAir to clear them).
param([switch]$All, [switch]$Carla, [switch]$Docker)

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
function Matching($pattern) { Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match $pattern } }

foreach ($p in @(@("controls", "fly_drone\.py"), @("stream", "stream_flight\.py"), @("traffic", "highway_violations\.py"))) {
    $procs = Matching $p[1]
    if ($procs) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ("{0,-9}: stopped" -f $p[0]) }
    else { Write-Host ("{0,-9}: not running" -f $p[0]) }
}

$stopArgs = @{}
if ($All) { $stopArgs["All"] = $true }
if ($Docker) { $stopArgs["Docker"] = $true }
& (Join-Path $repo "scripts\live_test\04_stop_stack.ps1") @stopArgs

if ($Carla) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "CarlaAir-v0.1.7-Windows11-x86_64\CarlaAir.ps1") --kill
}
