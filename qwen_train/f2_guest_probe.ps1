<#
.SYNOPSIS
  F2 guest infrastructure probe (Windows PowerShell 5.1 compatible). Emits JSON.

.DESCRIPTION
  Runs INSIDE the guest. Does NOT run Experiment J/F2 and does NOT invoke any F2
  arm/worker/orchestrator execution path. No Internet/Qdrant/learning system.

  Clock reference is ONLY the gateway's fresh HTTP `Date` header from the
  authorized `GET /v1/models` call. There is NO fallback to the manifest
  timestamp; missing Date => HOST_REFERENCE_MISSING, malformed =>
  HOST_REFERENCE_MALFORMED, both => clock FAIL.

  Readiness accounting is explicit: overall_status is PASS only when
  required_checks_failed = 0 AND required_checks_not_established = 0; otherwise
  FAIL (a required check failed) or NOT_READY (a required check could not be
  established).

.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\f2_guest_probe.ps1 `
      -GatewayIp 10.72.0.1 -GatewayPort 8099 -InputDisk F: -OutputPath C:\f2_probe.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GatewayIp,
    [int]$GatewayPort = 8099,
    [string]$InputDisk = "",
    [int[]]$DeniedHostPorts = @(6333, 8081, 135, 2179, 11435),
    [string]$DnsTestName = "",
    [string]$Ipv4TestHost = "",
    [int]$Ipv4TestPort = 443,
    [double]$ClockToleranceSeconds = 120.0,
    [string]$OutputPath = "C:\f2_probe_result.json"
)

$ErrorActionPreference = "Continue"
$failures = New-Object System.Collections.Generic.List[string]
$result = [ordered]@{
    schema_version                 = "f2-guest-probe/1"
    probe_started_utc              = (Get-Date).ToUniversalTime().ToString("o")
    host_reference_utc             = $null
    clock_reference_src            = "NOT_ESTABLISHED"
    clock_skew_seconds             = $null
    clock_status                   = "FAIL"
    clock_reason                   = "HOST_REFERENCE_MISSING"
    adapter_inventory              = @()
    adapter_count                  = $null
    route_inventory                = @()
    dns_inventory                  = @()
    dns_behavior                   = [ordered]@{ result = "NOT_ESTABLISHED" }
    authorized_flow                = [ordered]@{}
    denied_flows                   = @()
    arbitrary_ipv4                 = [ordered]@{ result = "NOT_ESTABLISHED" }
    ipv4                           = [ordered]@{ result = "NOT_ESTABLISHED" }
    ipv6                           = [ordered]@{ result = "NOT_ESTABLISHED" }
    icmp                           = [ordered]@{ result = "OBSERVED_BEHAVIOR" }
    input_disk_write               = [ordered]@{ result = "NOT_APPLICABLE" }
    internet                       = [ordered]@{}
    required_checks_total          = 0
    required_checks_passed         = 0
    required_checks_failed         = 0
    required_checks_not_established = 0
    overall_status                 = "FAIL"
    failure_reasons                = @()
}

$guestUtc = (Get-Date).ToUniversalTime()

# --- Inventory --------------------------------------------------------------
$result.adapter_inventory = @(Get-NetAdapter | Select-Object Name, Status, MacAddress, LinkSpeed)
$result.adapter_count = @($result.adapter_inventory).Count
$result.route_inventory = @(Get-NetRoute | Select-Object DestinationPrefix, NextHop, InterfaceAlias, RouteMetric)
$result.dns_inventory = @(Get-DnsClientServerAddress | Select-Object InterfaceAlias, AddressFamily, ServerAddresses)
$ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$ipv6 = @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin)
$result.ipv4 = $ipv4
$ipv4Default = @($result.route_inventory | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" }).Count
$ipv6Default = @($result.route_inventory | Where-Object { $_.DestinationPrefix -eq "::/0" }).Count
$result.internet = [ordered]@{ ipv4_default_route = $ipv4Default; ipv6_default_route = $ipv6Default }

# --- Authorized flow: TCP + GET /v1/models (yields the fresh Date reference) -
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
        try { $af.date_header = [string]$resp.Headers["Date"] } catch { $af.date_header = $null }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($resp.Content)
        $af.body_sha256 = ([System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)) -replace '-','').ToLower()
        $af.model_ids = @(($resp.Content | ConvertFrom-Json).data | ForEach-Object { $_.id })
    } catch { $af.http_error = $_.Exception.GetType().Name }
}
$result.authorized_flow = $af

# --- Clock: HTTP Date ONLY (no manifest fallback) ---------------------------
if ($af.date_header) {
    $result.clock_reference_src = "HTTP_DATE_HEADER"
    try {
        $ref = [datetime]::Parse($af.date_header).ToUniversalTime()
        $result.host_reference_utc = $ref.ToString("o")
        $skew = ($guestUtc - $ref).TotalSeconds
        $result.clock_skew_seconds = [math]::Round($skew, 3)
        if ([math]::Abs($skew) -le $ClockToleranceSeconds) { $result.clock_status = "OK"; $result.clock_reason = "" }
        else { $result.clock_reason = "GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW" }
    } catch { $result.clock_reason = "HOST_REFERENCE_MALFORMED" }
} else {
    $result.clock_reason = "HOST_REFERENCE_MISSING"
}

# --- DNS: configuration vs behavior -----------------------------------------
$dnsServers = @($result.dns_inventory | Where-Object { $_.ServerAddresses })
if ($dnsServers.Count -eq 0) {
    $result.dns_behavior = [ordered]@{ result = "NOT_APPLICABLE"; note = "no DNS servers configured (by design)" }
} elseif (-not $DnsTestName) {
    $result.dns_behavior = [ordered]@{ result = "NOT_ESTABLISHED"; note = "DNS servers configured but no safe test name supplied" }
} else {
    try {
        $null = Resolve-DnsName -Name $DnsTestName -ErrorAction Stop
        $result.dns_behavior = [ordered]@{ result = "FAIL"; note = "resolution succeeded unexpectedly" }
    } catch {
        $result.dns_behavior = [ordered]@{ result = "NOT_ESTABLISHED"; note = "resolution failed; NXDOMAIN/transport does not prove blocking"; error = $_.Exception.GetType().Name }
    }
}

# --- Arbitrary IPv4 connectivity (separate from route inspection) -----------
if (-not $Ipv4TestHost) {
    $result.arbitrary_ipv4 = [ordered]@{ result = "NOT_ESTABLISHED"; reason = "NO_SAFE_DESTINATION" }
} else {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($Ipv4TestHost, $Ipv4TestPort, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000)) { $c.EndConnect($iar); $result.arbitrary_ipv4 = [ordered]@{ result = "FAIL"; detail = "connected unexpectedly" } }
        else { $result.arbitrary_ipv4 = [ordered]@{ result = "PASS"; detail = "timeout as expected" } }
        $c.Close()
    } catch { $result.arbitrary_ipv4 = [ordered]@{ result = "PASS"; detail = "refused/blocked"; error = $_.Exception.GetType().Name } }
}
if ($ipv4Default -gt 0) { $result.arbitrary_ipv4 = [ordered]@{ result = "FAIL"; reason = "IPV4_DEFAULT_ROUTE_PRESENT" } }

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
}

# --- IPv6 (inventory vs connectivity) ---------------------------------------
$v6usable = @($ipv6 | Where-Object { $_.IPAddress -notlike "fe80*" -and $_.IPAddress -ne "::1" })
if ($v6usable.Count -gt 0) { $result.ipv6 = [ordered]@{ addresses = $ipv6; result = "INVENTORY_ONLY"; note = "global address present; connectivity not tested" } }
else { $result.ipv6 = [ordered]@{ addresses = $ipv6; result = "NOT_ESTABLISHED"; reason = "NO_USABLE_IPV6_DESTINATION" } }

# --- ICMP observed behavior (5.1-safe .NET Ping) ----------------------------
$icmp = [ordered]@{ gateway_ping = $null; result = "OBSERVED_BEHAVIOR"; note = "ping result is observed behavior, not causal ACL proof" }
try { $ping = New-Object System.Net.NetworkInformation.Ping; $reply = $ping.Send($GatewayIp, 1000); $icmp.gateway_ping = ($reply.Status -eq "Success") } catch { $icmp.gateway_ping = $null }
$result.icmp = $icmp

# --- Input disk write attempt -----------------------------------------------
if ($InputDisk) {
    $probeFile = Join-Path "$InputDisk" "f2_write_probe_$(Get-Random).tmp"
    try {
        Set-Content -LiteralPath $probeFile -Value "probe" -ErrorAction Stop
        $result.input_disk_write = [ordered]@{ target = $probeFile; result = "WRITE_SUCCEEDED_UNEXPECTED"; proves_read_only = $false }
    } catch {
        $hr = $_.Exception.HResult
        $result.input_disk_write = [ordered]@{ target = $probeFile; result = "WRITE_FAILED"; hresult = ("0x{0:X8}" -f $hr); error = $_.Exception.GetType().Name; message = $_.Exception.Message; proves_read_only = "NOT_ESTABLISHED" }
    }
}

# --- Required-check accounting (PASS only if none failed/not_established) ----
$required = @()

if ($result.clock_status -eq "OK") { $required += [pscustomobject]@{ name = "clock"; status = "PASS" } } else { $required += [pscustomobject]@{ name = "clock"; status = "FAIL" } }
if ($result.adapter_count -eq 1) { $required += [pscustomobject]@{ name = "one_nic"; status = "PASS" } } else { $required += [pscustomobject]@{ name = "one_nic"; status = "FAIL" } }
if (($ipv4Default + $ipv6Default) -eq 0) { $required += [pscustomobject]@{ name = "no_default_route"; status = "PASS" } } else { $required += [pscustomobject]@{ name = "no_default_route"; status = "FAIL" } }
if ($af.tcp_ok -and $af.http_status -eq 200) { $required += [pscustomobject]@{ name = "authorized_flow"; status = "PASS" } }
elseif (-not $af.tcp_ok) { $required += [pscustomobject]@{ name = "authorized_flow"; status = "NOT_ESTABLISHED" } }
else { $required += [pscustomobject]@{ name = "authorized_flow"; status = "FAIL" } }
$badDenied = @($result.denied_flows | Where-Object { $_.result -eq "CONNECTED_UNEXPECTED" }).Count
if ($badDenied -gt 0) { $required += [pscustomobject]@{ name = "denied_ports"; status = "FAIL" } } else { $required += [pscustomobject]@{ name = "denied_ports"; status = "PASS" } }
if ($dnsServers.Count -eq 0) { $required += [pscustomobject]@{ name = "dns"; status = "PASS" } }
elseif ($result.dns_behavior.result -eq "FAIL") { $required += [pscustomobject]@{ name = "dns"; status = "FAIL" } }
else { $required += [pscustomobject]@{ name = "dns"; status = "NOT_ESTABLISHED" } }
if ($InputDisk) {
    if ($result.input_disk_write.result -eq "WRITE_SUCCEEDED_UNEXPECTED") { $required += [pscustomobject]@{ name = "input_disk_readonly"; status = "FAIL" } }
    else { $required += [pscustomobject]@{ name = "input_disk_readonly"; status = "NOT_ESTABLISHED" } }
}

$passed = @($required | Where-Object { $_.status -eq "PASS" }).Count
$failed = @($required | Where-Object { $_.status -eq "FAIL" }).Count
$notEstablished = @($required | Where-Object { $_.status -eq "NOT_ESTABLISHED" }).Count
$result.required_checks_total = $required.Count
$result.required_checks_passed = $passed
$result.required_checks_failed = $failed
$result.required_checks_not_established = $notEstablished
$required | ForEach-Object { if ($_.status -ne "PASS") { $failures.Add("REQUIRED_" + $_.name + ":" + $_.status) } }

if ($failed -gt 0) { $result.overall_status = "FAIL" }
elseif ($notEstablished -gt 0) { $result.overall_status = "NOT_READY" }
else { $result.overall_status = "PASS" }

$result.failure_reasons = @($failures)
($result | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output ($result | ConvertTo-Json -Depth 8)
