<#
.SYNOPSIS
  F2 guest infrastructure probe. Runs INSIDE the guest; emits JSON.

.DESCRIPTION
  Validates the isolated F2 guest boundary. It does NOT run Experiment J/F2 and
  does NOT invoke any F2 arm/worker/orchestrator execution path. It requires no
  Internet, Qdrant, or learning system. It reads the input bundle manifest for
  host_reference_utc (never asks the host for the time), checks the guest clock,
  inventories the network, tests the single authorized flow
  (host model gateway /v1/models), and attempts the denied host ports.

.EXAMPLE
  ./qwen_train/f2_guest_probe.ps1 -GatewayIp 10.72.0.1 -GatewayPort 8099 `
      -ManifestPath D:\f2_bundle_manifest.json -DeniedHostPorts 6333,8081 `
      -OutputPath C:\f2_probe_result.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GatewayIp,
    [int]$GatewayPort = 8099,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [int[]]$DeniedHostPorts = @(6333, 8081),
    [double]$ClockToleranceSeconds = 120.0,
    [string]$OutputPath = "C:\f2_probe_result.json"
)

$ErrorActionPreference = "Continue"
$failures = New-Object System.Collections.Generic.List[string]
$result = [ordered]@{
    schema_version          = "f2-guest-probe/1"
    probe_started_utc       = (Get-Date).ToUniversalTime().ToString("o")
    host_reference_utc      = $null
    clock_skew_seconds      = $null
    clock_status            = "FAIL"
    adapter_inventory       = @()
    route_inventory         = @()
    dns_inventory           = @()
    authorized_flow         = [ordered]@{}
    denied_flows            = @()
    ipv4                    = [ordered]@{}
    ipv6                    = [ordered]@{}
    icmp                    = [ordered]@{}
    internet                = [ordered]@{}
    overall_status          = "FAIL"
    failure_reasons         = @()
}

# --- Clock check (offline; reference comes from the input bundle manifest) ----
$guestUtc = (Get-Date).ToUniversalTime()
try {
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $result.host_reference_utc = $manifest.host_reference_utc
    $ref = [datetime]::Parse($manifest.host_reference_utc).ToUniversalTime()
    $skew = ($guestUtc - $ref).TotalSeconds
    $result.clock_skew_seconds = [math]::Round($skew, 3)
    if ([math]::Abs($skew) -le $ClockToleranceSeconds) { $result.clock_status = "OK" }
    else { $failures.Add("GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW") }
} catch {
    $failures.Add("HOST_REFERENCE_MALFORMED")
}
if ($result.clock_status -ne "OK") {
    # Clock is a prerequisite: do not run network validation when it fails.
    $result.failure_reasons = @($failures)
    ($result | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $OutputPath -Encoding utf8
    Write-Output ($result | ConvertTo-Json -Depth 8)
    exit 1
}

# --- Network inventory (actual fetched data) --------------------------------
$result.adapter_inventory = @(Get-NetAdapter | Select-Object Name, Status, MacAddress, LinkSpeed)
$result.route_inventory = @(Get-NetRoute | Select-Object DestinationPrefix, NextHop, InterfaceAlias, RouteMetric)
$result.dns_inventory = @(Get-DnsClientServerAddress | Select-Object InterfaceAlias, AddressFamily, ServerAddresses)

$ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$ipv6 = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue |
    Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$result.ipv4 = $ipv4
$result.ipv6 = $ipv6

$defaultRoutes = @($result.route_inventory | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" -or $_.DestinationPrefix -eq "::/0" })
$result.internet = [ordered]@{
    ipv4_default_route = @($defaultRoutes | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" }).Count
    ipv6_default_route = @($defaultRoutes | Where-Object { $_.DestinationPrefix -eq "::/0" }).Count
}
if ($result.internet.ipv4_default_route -gt 0 -or $result.internet.ipv6_default_route -gt 0) {
    $failures.Add("UNEXPECTED_DEFAULT_ROUTE")
}

# --- Authorized flow: TCP + GET /v1/models ----------------------------------
$af = [ordered]@{ destination = "$GatewayIp`:$GatewayPort"; tcp_ok = $false; http_status = $null; body_sha256 = $null; model_ids = @() }
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect($GatewayIp, $GatewayPort, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(3000)) { $tcp.EndConnect($iar); $af.tcp_ok = $true }
    $tcp.Close()
} catch { $af.tcp_error = $_.Exception.GetType().Name }
if ($af.tcp_ok) {
    try {
        $resp = Invoke-WebRequest -Uri "http://$GatewayIp`:$GatewayPort/v1/models" -TimeoutSec 10 -UseBasicParsing
        $af.http_status = [int]$resp.StatusCode
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($resp.Content)
        $af.body_sha256 = ([System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)) -replace '-','').ToLower()
        $af.model_ids = @(($resp.Content | ConvertFrom-Json).data | ForEach-Object { $_.id })
    } catch { $af.http_error = $_.Exception.GetType().Name }
}
$result.authorized_flow = $af
if (-not $af.tcp_ok) { $failures.Add("AUTHORIZED_FLOW_TCP_FAILED") }

# --- Denied host ports (distinguish refused/timeout/unreachable) -------------
foreach ($p in $DeniedHostPorts) {
    $entry = [ordered]@{ destination = "$GatewayIp`:$p"; protocol = "TCP"; result = "UNKNOWN"; elapsed_ms = $null }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($GatewayIp, $p, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000)) { $c.EndConnect($iar); $entry.result = "CONNECTED_UNEXPECTED" }
        else { $entry.result = "TIMEOUT" }
        $c.Close()
    } catch [System.Net.Sockets.SocketException] {
        $msg = $_.Exception.Message
        if ($msg -match "refused") { $entry.result = "REFUSED" } elseif ($msg -match "unreachable") { $entry.result = "UNREACHABLE" } else { $entry.result = "SOCKET_ERROR" }
    } catch { $entry.result = "ERROR" }
    $sw.Stop(); $entry.elapsed_ms = [int]$sw.ElapsedMilliseconds
    $result.denied_flows += $entry
    if ($entry.result -eq "CONNECTED_UNEXPECTED") { $failures.Add("DENIED_PORT_REACHABLE:$p") }
}

# --- IPv6 / ICMP best-effort (only where meaningful) ------------------------
$v6 = @($ipv6 | Where-Object { $_.IPAddress -notlike "fe80*" -and $_.IPAddress -ne "::1" })
$result.ipv6 = [ordered]@{ addresses = $v6; live_enforcement = if ($v6.Count -gt 0) { "TESTABLE" } else { "NOT_ESTABLISHED" } }
$icmp = [ordered]@{ gateway_ping = $null; note = "ping failure != ACL proof without causality" }
try { $icmp.gateway_ping = (Test-Connection -TargetName $GatewayIp -Count 1 -Quiet -ErrorAction Stop) } catch { $icmp.gateway_ping = $false }
$result.icmp = $icmp

$result.failure_reasons = @($failures)
$result.overall_status = if ($failures.Count -eq 0) { "PASS" } else { "FAIL" }
($result | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output ($result | ConvertTo-Json -Depth 8)
