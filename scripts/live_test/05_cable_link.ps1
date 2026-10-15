# Two-machine live test, optional: a direct Ethernet cable between the two PCs instead of Wi-Fi / Tailscale (both PCs, as Administrator).
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\05_cable_link.ps1 -Role sim        (Afif's PC, 192.168.50.1)
#   powershell -ExecutionPolicy Bypass -File scripts\live_test\05_cable_link.ps1 -Role detector   (detector PC, 192.168.50.2)
#   ... -Undo   puts the port back on DHCP after the test
#
# A plain USB-C to USB-C cable gives no network between two PCs; it takes Ethernet ports (built in or a USB-C adapter) or USB4 / Thunderbolt
# networking on both sides. The detector PC also needs 02_open_firewall.ps1 -Cable. Then stream with --to 192.168.50.2.
param(
    [ValidateSet("sim", "detector")][string]$Role = "sim",
    [string]$Adapter = "",            # default: the first wired, physical adapter
    [switch]$Undo
)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "Run this window as Administrator (right-click PowerShell -> Run as administrator)." -ForegroundColor Red; exit 1 }

$nic = if ($Adapter) { Get-NetAdapter -Name $Adapter -ErrorAction SilentlyContinue }
       else { Get-NetAdapter -Physical | Where-Object { $_.MediaType -eq "802.3" } | Select-Object -First 1 }
if (-not $nic) { Write-Host "No wired adapter found. Pass -Adapter <name> (Get-NetAdapter lists them)." -ForegroundColor Red; exit 1 }
Write-Host "Adapter: $($nic.Name) ($($nic.InterfaceDescription)), status $($nic.Status)"

Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
if ($Undo) {
    Set-NetIPInterface -InterfaceIndex $nic.ifIndex -Dhcp Enabled
    Write-Host "Back on DHCP." -ForegroundColor Green; exit 0
}

$me, $peer = if ($Role -eq "sim") { "192.168.50.1", "192.168.50.2" } else { "192.168.50.2", "192.168.50.1" }
Set-NetIPInterface -InterfaceIndex $nic.ifIndex -Dhcp Disabled
New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $me -PrefixLength 24 | Out-Null   # no gateway: internet stays on Wi-Fi
Write-Host "This PC: $me on $($nic.Name)." -ForegroundColor Green
if ($nic.Status -ne "Up") { Write-Host "The cable is not connected yet (or the other end is off): plug it in, then: ping $peer" -ForegroundColor Yellow; exit 0 }
Start-Sleep -Seconds 3
if (Test-Connection $peer -Count 2 -Quiet) { Write-Host "ping $peer ok" -ForegroundColor Green }
else { Write-Host "no reply from $peer yet: run this script on the other PC too (ICMP may be blocked by its firewall; the stream test is what counts)" -ForegroundColor Yellow }
if ($Role -eq "sim") { Write-Host "Stream with:  python simulation\carla_scripts\stream_flight.py --to $peer --session <session>" }
