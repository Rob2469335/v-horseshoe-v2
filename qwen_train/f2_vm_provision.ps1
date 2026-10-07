<#
.SYNOPSIS
  Provision the dedicated Experiment-J / F2 Hyper-V isolation boundary.

.DESCRIPTION
  Creates a DEDICATED Hyper-V INTERNAL switch and a dedicated F2 guest VM with
  EXACTLY ONE network adapter, a default-deny network policy, and a single
  stateful allowed flow (F2 guest -> host model gateway TCP). It NEVER creates an
  External switch, NEVER creates NAT/uplink, and NEVER shares a host folder.

  DELIBERATELY NON-EXECUTING by default: prints the plan unless -Execute is given.
  Refuses unless a legitimate guest media source is supplied (a prepared golden
  VHDX via -GoldenVhdx, OR an official Windows install ISO via -IsoPath). It does
  NOT download anything.

  Authority: docs/EXPERIMENT_J_F2_VM_ISOLATION.md and F2-IMPL-AUTH-027. Running
  this script is REQUIRES AUTHORIZATION.

.PARAMETER Execute
  Actually perform the changes. Without it, only the plan is printed.

.PARAMETER IsoPath
  Path to the OFFICIAL Windows install ISO (SHA-256 verified by the operator
  before use). A fresh OS VHDX is created and the ISO is attached read-only as
  install media. Mutually exclusive with -GoldenVhdx.

.PARAMETER GoldenVhdx
  Path to a prepared F2 golden OS disk (Gen2, Secure Boot-compatible). Copied to
  the VM folder (never attached in place). Mutually exclusive with -IsoPath.

.EXAMPLE
  ./qwen_train/f2_vm_provision.ps1

.EXAMPLE
  ./qwen_train/f2_vm_provision.ps1 -Execute -IsoPath E:\iso\Win11_Enterprise_25H2_eval.iso
#>
[CmdletBinding()]
param(
    [switch]$Execute,
    [string]$IsoPath = "",
    [string]$GoldenVhdx = "",

    [string]$SwitchName = "F2-Internal-Switch",
    [string]$VmName = "F2-Isolation-VM",
    [string]$VmFolder = "C:\ProgramData\Microsoft\Windows\Hyper-V\F2",

    # Dedicated private subnet (NOT the Default Switch 172.28.x.x, NOT the LAN).
    [string]$HostGatewayIp = "10.72.0.1",
    [string]$GuestIp = "10.72.0.2",
    [string]$PrefixLength = "24",

    # Host model gateway (qwen_train/f2_model_gateway.py) bind + upstream.
    [int]$GatewayPort = 8099,
    [string]$UpstreamBaseUrl = "http://127.0.0.1:8080",

    [int]$ProcessorCount = 2,
    [int64]$MemoryStartupBytes = 4GB,
    [int64]$OsDiskSizeBytes = 64GB,

    # Hyper-V stateful ACL idle session timeout, in SECONDS. Must exceed the
    # longest upstream application timeout (qwen_train/f2_isolation_contract.py):
    # 1800 s > 960 s gateway > 900 s model streaming client ceiling.
    [int]$IdleSessionTimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"

function Write-Plan($msg) { Write-Host "[plan] $msg" }
function Write-Step($msg) { Write-Host "[exec] $msg" }

Write-Host "== F2 Hyper-V isolation provisioning =="
Write-Host "Switch      : $SwitchName (Internal ONLY; no External, no NAT, no uplink)"
Write-Host "VM          : $VmName (Generation 2; EXACTLY ONE NIC)"
Write-Host "Subnet      : $HostGatewayIp/$PrefixLength (host) <-> $GuestIp/$PrefixLength (guest)"
Write-Host "Allowed     : guest $GuestIp -> host gateway $HostGatewayIp`:$GatewayPort TCP (stateful, idle $IdleSessionTimeoutSeconds s)"
Write-Host "Denied      : everything else, BOTH directions, IPv4+IPv6 (Qdrant :6333, embedding :8081, LAN, internet, ICMP)"
Write-Host "Gateway     : F2_GATEWAY_BIND_HOST=$HostGatewayIp F2_GATEWAY_BIND_PORT=$GatewayPort F2_GATEWAY_ALLOWED_CLIENT=$GuestIp F2_MODEL_UPSTREAM=$UpstreamBaseUrl"
Write-Host "Media       : IsoPath='$IsoPath' GoldenVhdx='$GoldenVhdx'"
Write-Host ""

if (-not $Execute) {
    Write-Plan "Dry run. Re-run with -Execute and exactly one media source to provision."
    Write-Plan "Prerequisite: a legitimate guest media source. This script never downloads one."
    exit 0
}

# --- Guards (fail closed) ---------------------------------------------------
if ([string]::IsNullOrWhiteSpace($IsoPath) -eq [string]::IsNullOrWhiteSpace($GoldenVhdx)) {
    throw "F2 provisioning refused: supply EXACTLY ONE of -IsoPath or -GoldenVhdx."
}
if ($IsoPath -and -not (Test-Path -LiteralPath $IsoPath)) {
    throw "F2 provisioning refused: ISO not found: $IsoPath"
}
if ($GoldenVhdx -and -not (Test-Path -LiteralPath $GoldenVhdx)) {
    throw "F2 provisioning refused: golden VHDX not found: $GoldenVhdx"
}
if ($GuestIp -eq $HostGatewayIp) {
    throw "F2 provisioning refused: host and guest addresses must differ."
}
if (-not (Test-Path -LiteralPath $VmFolder)) { New-Item -ItemType Directory -Path $VmFolder | Out-Null }

# --- Internal switch (never External, never NAT) ----------------------------
$switch = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if (-not $switch) {
    Write-Step "Creating INTERNAL switch '$SwitchName'"
    New-VMSwitch -Name $SwitchName -SwitchType Internal | Out-Null
} elseif ($switch.SwitchType -ne "Internal") {
    throw "F2 provisioning refused: switch '$SwitchName' exists but is $($switch.SwitchType), not Internal."
} else {
    Write-Step "Internal switch '$SwitchName' already exists"
}

# Host vEthernet address on the switch (NO default gateway => no external route).
$hostIf = Get-NetAdapter -Name "vEthernet ($SwitchName)" -ErrorAction SilentlyContinue
if (-not $hostIf) {
    Write-Plan "Host vEthernet adapter not present yet (appears shortly after switch creation). Re-run."
    exit 0
}
if (-not (Get-NetIPAddress -InterfaceIndex $hostIf.ifIndex -IPAddress $HostGatewayIp -ErrorAction SilentlyContinue)) {
    Write-Step "Assigning host gateway $HostGatewayIp/$PrefixLength (no default gateway)"
    New-NetIPAddress -InterfaceIndex $hostIf.ifIndex -IPAddress $HostGatewayIp -PrefixLength ([int]$PrefixLength) | Out-Null
}
# No DNS servers that could enable general resolution.
Set-DnsClientServerAddress -InterfaceIndex $hostIf.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue

# --- VM (Generation 2) ------------------------------------------------------
$vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
if (-not $vm) {
    Write-Step "Creating Generation-2 VM '$VmName' (this attaches ONE adapter via -SwitchName)"
    New-VM -Name $VmName -Generation 2 -MemoryStartupBytes $MemoryStartupBytes `
        -SwitchName $SwitchName -Path $VmFolder | Out-Null
} else {
    Write-Step "VM '$VmName' already exists"
}

Set-VMProcessor -VMName $VmName -Count $ProcessorCount
Set-VMMemory -VMName $VmName -DynamicMemoryEnabled $false

# Secure Boot ON; vTPM enabled with a local key protector.
Set-VMFirmware -VMName $VmName -EnableSecureBoot On
Set-VMKeyProtector -VMName $VmName -NewLocalKeyProtector
Enable-VMTPM -VMName $VmName

# --- EXACTLY ONE NIC (fail closed) -----------------------------------------
# New-VM -SwitchName already created exactly one adapter. Do NOT add another.
$adapters = @(Get-VMNetworkAdapter -VMName $VmName)
if ($adapters.Count -ne 1) {
    throw "F2 provisioning refused: VM '$VmName' has $($adapters.Count) NICs; exactly 1 is required."
}
$nic = $adapters[0]
Set-VMNetworkAdapter -VMName $VmName -Name $nic.Name -SwitchName $SwitchName -DeviceNaming On
Set-VMNetworkAdapter -VMName $VmName -DhcpGuard On -RouterGuard On -MacAddressSpoofing Off
Write-Step "NIC '$($nic.Name)' on switch '$SwitchName' (single adapter verified)"

# --- Media ------------------------------------------------------------------
if ($GoldenVhdx) {
    $targetVhdx = Join-Path $VmFolder "$VmName-os.vhdx"
    if (-not (Test-Path -LiteralPath $targetVhdx)) {
        Write-Step "Copying golden VHDX to $targetVhdx (source left untouched)"
        Copy-Item -LiteralPath $GoldenVhdx -Destination $targetVhdx
    }
    if (-not (Get-VMHardDiskDrive -VMName $VmName -ErrorAction SilentlyContinue)) {
        Add-VMHardDiskDrive -VMName $VmName -Path $targetVhdx
    }
} else {
    $osVhdx = Join-Path $VmFolder "$VmName-os.vhdx"
    if (-not (Test-Path -LiteralPath $osVhdx)) {
        Write-Step "Creating blank OS disk $osVhdx ($([math]::Round($OsDiskSizeBytes/1GB)) GB dynamic)"
        New-VHD -Path $osVhdx -SizeBytes $OsDiskSizeBytes -Dynamic | Out-Null
    }
    if (-not (Get-VMHardDiskDrive -VMName $VmName -ErrorAction SilentlyContinue)) {
        Add-VMHardDiskDrive -VMName $VmName -Path $osVhdx
    }
    $dvd = Get-VMDvdDrive -VMName $VmName -ErrorAction SilentlyContinue
    if (-not $dvd) {
        Write-Step "Attaching install ISO read-only: $IsoPath"
        $dvd = Add-VMDvdDrive -VMName $VmName -Path $IsoPath
    }
    Set-VMFirmware -VMName $VmName -FirstBootDevice $dvd
    Write-Plan "ISO attached for installation. After install succeeds, DETACH the DVD (Remove-VMDvdDrive) and re-run to finalize."
}

# --- Default-deny network policy (extended ACLs) ----------------------------
# Stateful allow FIRST (higher weight), then catch-all deny in BOTH directions.
if ((Get-VM -Name $VmName).State -ne "Off") {
    Write-Plan "VM is $((Get-VM -Name $VmName).State); stop it to apply ACLs, then re-run."
} else {
    # Remove any prior rules (idempotent re-run).
    foreach ($acl in @(Get-VMNetworkAdapterExtendedAcl -VMName $VmName -ErrorAction SilentlyContinue)) {
        try {
            Remove-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction $acl.Direction -Action $acl.Action `
                -LocalIPAddress $acl.LocalIPAddress -RemoteIPAddress $acl.RemoteIPAddress `
                -LocalPort $acl.LocalPort -RemotePort $acl.RemotePort -Protocol $acl.Protocol `
                -Weight $acl.Weight -ErrorAction SilentlyContinue
        } catch { }
    }
    # Microsoft semantics (verified): extended ACLs are STATELESS unless
    # -Stateful $true is passed; -IdleSessionTimeout is an Int32 number of
    # SECONDS (not a TimeSpan). Explicit IPv4+IPv6 catch-all coverage uses the
    # documented wildcard ANY (== 0.0.0.0/0 plus ::/0).
    Write-Step "ACL: ALLOW outbound guest->gateway TCP $GatewayPort, STATEFUL (weight 100, idle 300s)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Outbound -Action Allow `
        -LocalIPAddress $GuestIp -RemoteIPAddress $HostGatewayIp -RemotePort $GatewayPort `
        -Protocol TCP -Weight 100 -Stateful $true -IdleSessionTimeout $IdleSessionTimeoutSeconds | Out-Null
    Write-Step "ACL: DENY outbound catch-all ANY (IPv4+IPv6), all protocols (weight 1)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Outbound -Action Deny `
        -LocalIPAddress ANY -RemoteIPAddress ANY -Weight 1 | Out-Null
    Write-Step "ACL: DENY inbound catch-all ANY (IPv4+IPv6), all protocols (weight 1)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Inbound -Action Deny `
        -LocalIPAddress ANY -RemoteIPAddress ANY -Weight 1 | Out-Null

    # --- Read-back + fail-closed verification --------------------------------
    Write-Host ""
    Write-Host "Stored extended ACLs (proof table):"
    $storedAcls = @(Get-VMNetworkAdapterExtendedAcl -VMName $VmName)
    $storedAcls | Select-Object Direction, Action, LocalIPAddress, RemoteIPAddress, `
        LocalPort, RemotePort, Protocol, Weight, Stateful, IdleSessionTimeout |
        Format-Table -AutoSize

    $allow = @($storedAcls | Where-Object { $_.Direction -eq "Outbound" -and $_.Action -eq "Allow" })
    if ($allow.Count -ne 1) {
        throw "F2 ACL verification FAILED: expected exactly 1 outbound Allow rule, found $($allow.Count)."
    }
    if (-not ($allow[0].Stateful -eq $true)) {
        throw "F2 ACL verification FAILED: outbound Allow rule is not Stateful."
    }
    if ([int]$allow[0].IdleSessionTimeout -le 0) {
        throw "F2 ACL verification FAILED: outbound Allow rule has no idle session timeout."
    }
    $denyOut = @($storedAcls | Where-Object { $_.Action -eq "Deny" -and $_.Direction -eq "Outbound" })
    $denyIn = @($storedAcls | Where-Object { $_.Action -eq "Deny" -and $_.Direction -eq "Inbound" })
    if ($denyOut.Count -lt 1) { throw "F2 ACL verification FAILED: no outbound catch-all deny." }
    if ($denyIn.Count -lt 1) { throw "F2 ACL verification FAILED: no inbound catch-all deny." }
    if ([int]$allow[0].Weight -le [int]$denyOut[0].Weight) {
        throw "F2 ACL verification FAILED: allow weight ($($allow[0].Weight)) does not outrank deny weight ($($denyOut[0].Weight))."
    }
    Write-Step "ACL verification passed: stateful allow (w=$($allow[0].Weight)) > catch-all deny (w=$($denyOut[0].Weight)); inbound+outbound deny present"
}

# --- Integration services (final side-channel state) ------------------------
Write-Host "Integration services BEFORE:"
Get-VMIntegrationService -VMName $VmName | Select-Object Name, Enabled | Format-Table -AutoSize
foreach ($svc in @("Guest Service Interface", "Hyper-V PowerShell Direct", "Data Exchange", "VSS", "Time Synchronization")) {
    Disable-VMIntegrationService -VMName $VmName -Name $svc -ErrorAction SilentlyContinue
}
Write-Host "Integration services AFTER (Heartbeat + Guest Shutdown intentionally left ENABLED):"
Get-VMIntegrationService -VMName $VmName | Select-Object Name, Enabled | Format-Table -AutoSize

# --- Final single-NIC verification (fail closed) ----------------------------
$final = @(Get-VMNetworkAdapter -VMName $VmName)
if ($final.Count -ne 1) {
    throw "F2 provisioning FAILED verification: $($final.Count) NICs present; expected exactly 1."
}

Write-Host ""
Write-Host "F2 isolation boundary provisioned. Guest-side steps (NOT automated):"
Write-Host "  1. Install Windows (official ISO) via VMConnect; set static guest IP $GuestIp/$PrefixLength, NO default gateway, NO DNS."
Write-Host "  2. After install: detach the DVD, re-run to finalize, boot the guest offline."
Write-Host "  3. Start the model gateway on the host:"
Write-Host "     `$env:F2_GATEWAY_BIND_HOST='$HostGatewayIp'; `$env:F2_GATEWAY_BIND_PORT='$GatewayPort';"
Write-Host "     `$env:F2_GATEWAY_ALLOWED_CLIENT='$GuestIp'; `$env:F2_MODEL_UPSTREAM='$UpstreamBaseUrl';"
Write-Host "     python -m qwen_train.f2_model_gateway"
Write-Host "  4. In the guest set SWARM_F2_ISOLATION=1 and run P2 via the F2 worker."
Write-Host "  5. Prove guest policy from INSIDE the guest (not PowerShell Direct): TCP 8099 only; all else blocked."
