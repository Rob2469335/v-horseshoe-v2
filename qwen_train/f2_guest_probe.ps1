<#
.SYNOPSIS
  F2 guest infrastructure probe. Runs INSIDE the guest; emits JSON.

.DESCRIPTION
  Validates the isolated F2 guest boundary. It does NOT run Experiment J/F2 and
  does NOT invoke any F2 arm/worker/orchestrator execution path. It requires no
  Internet, Qdrant, or learning system.

  Clock reference is the gateway's FRESH HTTP `Date` header from the authorized
  `GET /v1/models` call (UTC) -- no KVP, no PowerShell Direct, no extra channel.
  The input-bundle manifest timestamp is retained as provenance only.

  Honest semantics: unavailable/meaningless tests are reported as
  NOT_ESTABLISHED / NOT_APPLICABLE, never forced into PASS/FAIL.

.EXAMPLE
  ./qwen_train/f2_guest_probe.ps1 -GatewayIp 10.72.0.1 -GatewayPort 8099 `
      -ManifestPath D:\f2_bundle_manifest.json -InputDisk F: `
      -DeniedHostPorts 6333,6333,8081,135,2179,11435 -OutputPath C:\f2_probe.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GatewayIp,
    [int]$GatewayPort = 8099,
    [string]$ManifestPath = "",
    [string]$InputDisk = "",
    [int[]]$DeniedHostPorts = @(6333, 8081, 135, 2179, 11435),
    [double]$ClockToleranceSeconds = 120.0,
    [string]$OutputPath = "C:\f2_probe_result.json"
)

$ErrorActionPreference = "Continue"
$failures = New-Object System.Collections.Generic.List[string]
$result = [ordered]@{
    schema_version      = "f2-guest-probe/1"
    probe_started_utc   = (Get-Date).ToUniversalTime().ToString("o")
    host_reference_utc  = $null
    clock_reference_src = "NOT_ESTABLISHED"
    clock_skew_seconds  = $null
    clock_status        = "FAIL"
    clock_reason        = "HOST_REFERENCE_MISSING"
    adapter_inventory   = @()
    route_inventory     = @()
    dns_inventory       = @()
    authorized_flow     = [ordered]@{}
    denied_flows        = @()
    arbitrary_ipv4      = [ordered]@{ result = "NOT_ESTABLISHED" }
    dns_behavior        = [ordered]@{ result = "NOT_ESTABLISHED" }
    ipv4                = [ordered]@{}
    ipv6                = [ordered]@{ result = "NOT_ESTABLISHED" }
    icmp                = [ordered]@{ result = "NOT_ESTABLISHED" }
    input_disk_write    = [ordered]@{ result = "NOT_APPLICABLE" }
    internet            = [ordered]@{}
    overall_status      = "FAIL"
    failure_reasons     = @()
}

# --- Network inventory ------------------------------------------------------
$result.adapter_inventory = @(Get-NetAdapter | Select-Object Name, Status, MacAddress, LinkSpeed)
$result.route_inventory = @(Get-NetRoute | Select-Object DestinationPrefix, NextHop, InterfaceAlias, RouteMetric)
$result.dns_inventory = @(Get-DnsClientServerAddress | Select-Object InterfaceAlias, AddressFamily, ServerAddresses)
$ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$ipv6 = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$result.ipv4 = $ipv4
$defaultRoutes = @($result.route_inventory | Where-Object { $_.DestinationPrefix -in @("0.0.0.0/0", "::/0") })
$result.internet = [ordered]@{
    ipv4_default_route = @($defaultRoutes | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" }).Count
    ipv6_default_route = @($defaultRoutes | Where-Object { $_.DestinationPrefix -eq "::/0" }).Count
}
if ($result.internet.ipv4_default_route -gt 0) { $failures.Add("UNEXPECTED_IPV4_DEFAULT_ROUTE") }
if ($result.internet.ipv6_default_route -gt 0) { $failures.Add("UNEXPECTED_IPV6_DEFAULT_ROUTE") }

# --- Authorized flow: TCP + GET /v1/models (also yields the fresh Date ref) --
$af = [ordered]@{ destination = "$GatewayIp`:$GatewayPort"; tcp_ok = $false; http_status = $null; date_header = $null; body_sha256 = $null; model_ids = @() }
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
        if ($resp.Headers.ContainsKey("Date")) { $af.date_header = [string]$resp.Headers["Date"] }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($resp.Content)
        $af.body_sha256 = ([System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)) -replace '-','').ToLower()
        $af.model_ids = @(($resp.Content | ConvertFrom-Json).data | ForEach-Object { $_.id })
    } catch { $af.http_error = $_.Exception.GetType().Name }
}
$result.authorized_flow = $af
if (-not $af.tcp_ok) { $failures.Add("AUTHORIZED_FLOW_TCP_FAILED") }

# --- Clock: prefer the FRESH gateway Date header; manifest is provenance -----
if ($af.date_header) {
    $result.clock_reference_src = "HTTP_DATE_HEADER"
    $result.host_reference_utc = ([datetime]::Parse($af.date_header)).ToUniversalTime().ToString("o")
} elseif ($ManifestPath -and (Test-Path -LiteralPath $ManifestPath)) {
    try {
        $m = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
        $result.clock_reference_src = "BUNDLE_MANIFEST_PROVENANCE"
        $result.host_reference_utc = $m.host_reference_utc
    } catch { $result.clock_reason = "HOST_REFERENCE_MALFORMED" }
}
try {
    $guestUtc = (Get-Date).ToUniversalTime()
    $ref = [datetime]::Parse($result.host_reference_utc).ToUniversalTime()
    $skew = ($guestUtc - $ref).TotalSeconds
    $result.clock_skew_seconds = [math]::Round($skew, 3)
    if ([math]::Abs($skew) -le $ClockToleranceSeconds) { $result.clock_status = "OK"; $result.clock_reason = "" }
    else { $result.clock_reason = "GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW" }
} catch { if ($result.clock_reason -eq "HOST_REFERENCE_MISSING") { $result.clock_reason = "HOST_REFERENCE_MALFORMED" } }
if ($result.clock_status -ne "OK") { $failures.Add($result.clock_reason) }

# --- DNS behavior (not just enumeration) ------------------------------------
$dnsConfigured = @($result.dns_inventory | Where-Object { $_.ServerAddresses })
if ($dnsConfigured.Count -eq 0) { $result.dns_behavior = [ordered]@{ result = "NOT_APPLICABLE"; note = "no DNS servers configured by design" } }
else {
    try {
        $q = Resolve-DnsName -Name "example.invalid" -ErrorAction Stop
        $result.dns_behavior = [ordered]@{ result = "FAIL"; note = "DNS resolved with no servers expected"; detail = @($q) }
        $failures.Add("DNS_UNEXPECTEDLY_FUNCTIONAL")
    } catch { $result.dns_behavior = [ordered]@{ result = "PASS"; note = "DNS resolution failed as expected"; error = $_.Exception.GetType().Name } }
}

# --- Arbitrary IPv4 egress --------------------------------------------------
if ($result.internet.ipv4_default_route -eq 0) {
    $result.arbitrary_ipv4 = [ordered]@{ result = "NOT_ESTABLISHED"; note = "no IPv4 default route; no safe external destination" }
} else {
    $result.arbitrary_ipv4 = [ordered]@{ result = "FAIL"; note = "IPv4 default route present" }
    $failures.Add("IPV4_DEFAULT_ROUTE_PRESENT")
}

# --- Denied host ports ------------------------------------------------------
foreach ($p in $DeniedHostPorts) {
    $entry = [ordered]@{ destination = "$GatewayIp`:$p"; protocol = "TCP"; result = "NOT_ESTABLISHED"; elapsed_ms = $null }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($GatewayIp, $p, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000)) { $c.EndConnect($iar); $entry.result = "CONNECTED_UNEXPECTED" }
        else { $entry.result = "TIMEOUT" }
        $c.Close()
    } catch [System.Net.Sockets.SocketException] {
        if ($_.Exception.Message -match "refused") { $entry.result = "REFUSED" }
        elseif ($_.Exception.Message -match "unreachable") { $entry.result = "UNREACHABLE" }
        else { $entry.result = "ERROR" }
    } catch { $entry.result = "ERROR" }
    $sw.Stop(); $entry.elapsed_ms = [int]$sw.ElapsedMilliseconds
    $result.denied_flows += $entry
    if ($entry.result -eq "CONNECTED_UNEXPECTED") { $failures.Add("DENIED_PORT_REACHABLE:$p") }
}

# --- IPv6 / ICMP (only where meaningful) ------------------------------------
$v6usable = @($ipv6 | Where-Object { $_.IPAddress -notlike "fe80*" -and $_.IPAddress -ne "::1" })
$result.ipv6 = [ordered]@{ addresses = $ipv6; result = if ($v6usable.Count -gt 0) { "TESTABLE" } else { "NOT_ESTABLISHED" }; note = "no usable global IPv6 destination => live enforcement NOT_ESTABLISHED" }
$icmp = [ordered]@{ gateway_ping = $null; result = "NOT_ESTABLISHED"; note = "ping result is observed behavior, not causal ACL proof" }
try { $icmp.gateway_ping = (Test-Connection -TargetName $GatewayIp -Count 1 -Quiet -ErrorAction Stop); $icmp.result = "OBSERVED" } catch { $icmp.result = "NOT_ESTABLISHED" }
$result.icmp = $icmp

# --- Input disk write attempt (guest-side read-only evidence) ---------------
if ($InputDisk) {
    $probeFile = Join-Path "$InputDisk" "f2_write_probe_$(Get-Random).tmp"
    try {
        Set-Content -LiteralPath $probeFile -Value "probe" -ErrorAction Stop
        $result.input_disk_write = [ordered]@{ target = $probeFile; result = "WRITE_SUCCEEDED_UNEXPECTED"; proves_read_only = $false }
        $failures.Add("INPUT_DISK_WRITABLE")
    } catch {
        $result.input_disk_write = [ordered]@{ target = $probeFile; result = "WRITE_FAILED_EXPECTED"; error = $_.Exception.GetType().Name; message = $_.Exception.Message; proves_read_only = "CANDIDATE — confirm error indicates read-only, not ACL/permission" }
    }
} else {
    $result.input_disk_write = [ordered]@{ result = "NOT_APPLICABLE" }
}

$result.failure_reasons = @($failures)
$result.overall_status = if ($failures.Count -eq 0) { "PASS" } else { "FAIL" }
($result | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output ($result | ConvertTo-Json -Depth 8)
