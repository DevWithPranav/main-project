# Two-machine live test, step 1 (detector PC): is Tailscale up, what is this PC's address, can it see Afif, is the port open?
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\01_check_network.ps1 [-Peer zenith]
param([string]$Peer = "zenith")

$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts) { $ts = Get-Item "C:\Program Files\Tailscale\tailscale.exe" -ErrorAction SilentlyContinue }
if (-not $ts) { Write-Host "Tailscale is not installed on this PC." -ForegroundColor Red; exit 1 }
$tsExe = if ($ts.Source) { $ts.Source } else { $ts.FullName }

Write-Host "== Tailscale ==" -ForegroundColor Cyan
$status = & $tsExe status 2>&1 | Out-String
Write-Host $status
$myIp = (& $tsExe ip -4 2>$null | Select-Object -First 1)
if (-not $myIp) {
    Write-Host "This PC has no Tailscale address: Tailscale is not connected." -ForegroundColor Red
    Write-Host "  Open the Tailscale tray icon and sign in / 'Connect', or run:  tailscale up"
    exit 1
}
Write-Host "This PC (detector): $myIp" -ForegroundColor Green

Write-Host "`n== $Peer ==" -ForegroundColor Cyan
$peerIp = (& $tsExe ip -4 $Peer 2>$null | Select-Object -First 1)
if (-not $peerIp) {
    Write-Host "Cannot find a peer named '$Peer' in this tailnet. Names seen:" -ForegroundColor Yellow
    ($status -split "`n") | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+' } | ForEach-Object { "  " + $_.Trim() }
    Write-Host "Re-run with -Peer <name>."
} else {
    Write-Host "$Peer address: $peerIp"
    Write-Host "tailscale ping (3 tries; 'via DERP' means a relayed, slower path; 'direct' is what we want):"
    & $tsExe ping -c 3 --timeout 5s $Peer 2>&1 | ForEach-Object { "  $_" }
}

Write-Host "`n== This PC's side ==" -ForegroundColor Cyan
$rule = Get-NetFirewallRule -DisplayName "Aerial live test (Tailscale)" -ErrorAction SilentlyContinue
if ($rule) { Write-Host "Firewall rule: present ($($rule.Enabled))" -ForegroundColor Green }
else { Write-Host "Firewall rule: missing. Run (as Administrator): scripts\live_test\02_open_firewall.ps1" -ForegroundColor Yellow }
foreach ($port in 5555, 5556, 8000, 5173) {
    $l = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    $what = @{ 5555 = "pipeline (frames from Afif)"; 5556 = "net_check echo server"; 8000 = "backend"; 5173 = "dashboard" }[$port]
    if ($l) { Write-Host ("  :{0,-5} listening on {1,-10} {2}" -f $port, $l.LocalAddress, $what) } else { Write-Host ("  :{0,-5} not listening     {1}" -f $port, $what) }
}
Write-Host "`nClock: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff')  (Afif's PC should show the same second; net_check.py ping measures the exact difference)"
Write-Host "`nNext:  scripts\live_test\02_open_firewall.ps1 (once, as Administrator), then run the network check with Afif (see docs\Two_Machine_Live_Test.md, step 3)."
