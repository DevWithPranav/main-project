# Two-machine live test, step 2 (detector PC, once, as Administrator): let Afif's PC reach the live-feed port.
#   Right-click PowerShell -> Run as administrator, then:
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\02_open_firewall.ps1 [-Dashboard] [-Remove]
#
# The rule allows inbound TCP only from the Tailscale address range (100.64.0.0/10), i.e. only machines in your tailnet:
#   5555  frames from the simulator PC (services/live/pipeline.py)
#   5556  the network check (services/live/net_check.py listen)
#   with -Dashboard also 8000 (backend) and 5173 (dashboard), so Afif can open the dashboard in his browser too.
param([switch]$Dashboard, [switch]$Remove)

$name = "Aerial live test (Tailscale)"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "Run this window as Administrator (right-click PowerShell -> Run as administrator)." -ForegroundColor Red; exit 1 }

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) { Write-Host "Rule removed." -ForegroundColor Green; exit 0 }

$ports = @(5555, 5556)
if ($Dashboard) { $ports += 8000, 5173 }
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ports -RemoteAddress "100.64.0.0/10" -Profile Any | Out-Null
Write-Host "Allowed inbound TCP $($ports -join ', ') from 100.64.0.0/10 (the tailnet only)." -ForegroundColor Green
Write-Host "Remove it after the test:  scripts\live_test\02_open_firewall.ps1 -Remove"
