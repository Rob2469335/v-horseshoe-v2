<#
.SYNOPSIS
  Provision the dedicated Experiment-J / F2 Hyper-V isolation boundary.

.DESCRIPTION
  Creates a DEDICATED Hyper-V INTERNAL switch and a dedicated F2 guest VM, with a
  default-deny network policy and a single allowed flow (F2 guest -> host model
  gateway TCP). It NEVER creates an External switch, NEVER creates NAT, and NEVER
  adds an internet uplink. The main repository is never mounted into the guest.

  This script is DELIBERATELY NON-EXECUTING by default: it prints the plan unless
  -Execute is passed. It refuses to run at all unless a legitimate guest OS image
  is supplied (-ImagePath). It does not download images.

  Authority: docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md
  (F2-IMPL-AUTH-027). Running it is REQUIRES AUTHORIZATION.

.PARAMETER Execute
  Actually perform the changes. Without it, only the plan is printed.

.PARAMETER ImagePath
  Path to a legitimate guest OS image (VHDX for a prepared F2 golden image, or an
  ISO/installation VHDX). REQUIRED for -Execute.

.EXAMPLE
  # Plan only (safe):
  ./qwen_train/f2_vm_provision.ps1

.EXAMPLE
  # Execute once an image exists and authorization is recorded:
  ./qwen_train/f2_vm_provision.ps1 -Execute -ImagePath D:\f2\f2-golden.vhdx
#>
[CmdletBinding()]
param(
    [switch]$Execute,
    [string]$ImagePath = "",

    [string]$SwitchName = "F2-Internal-Switch",
    [string]$VmName = "F2-Isolation-VM",

    # Dedicated private subnet (NOT the Default Switch 172.28.x.x, NOT the LAN).
    [string]$HostGatewayIp = "10.72.0.1",
    [string]$GuestIp = "10.72.0.2",
    [string]$PrefixLength = "24",

    # Host model gateway (qwen_train/f2_model_gateway.py) bind + upstream.
    [int]$GatewayPort = 8099,
    [string]$UpstreamBaseUrl = "http://127.0.0.1:8080",

    [int]$ProcessorCount = 2,
    [int64]$MemoryStartupBytes = 4GB
)

$ErrorActionPreference = "Stop"

function Write-Plan($msg) { Write-Host "[plan] $msg" }
function Write-Step($msg) { Write-Host "[exec] $msg" }

Write-Host "== F2 Hyper-V isolation provisioning =="
Write-Host "Switch      : $SwitchName (Internal ONLY; no External, no NAT)"
Write-Host "VM          : $VmName (Generation 2)"
Write-Host "Subnet      : $HostGatewayIp/$PrefixLength (host) <-> $GuestIp/$PrefixLength (guest)"
Write-Host "Allowed     : guest $GuestIp -> host gateway $HostGatewayIp`:$GatewayPort TCP"
Write-Host "Denied      : everything else (Qdrant :6333, embedding :8081, LAN, internet)"
Write-Host "Gateway     : F2_GATEWAY_BIND_HOST=$HostGatewayIp F2_GATEWAY_BIND_PORT=$GatewayPort F2_GATEWAY_ALLOWED_CLIENT=$GuestIp F2_MODEL_UPSTREAM=$UpstreamBaseUrl"
Write-Host ""

if (-not $Execute) {
    Write-Plan "Dry run. Re-run with -Execute -ImagePath <path> to provision."
    Write-Plan "Prerequisite: a legitimate guest OS image. This script never downloads one."
    exit 0
}

# --- Guards (fail closed) ---------------------------------------------------
if ([string]::IsNullOrWhiteSpace($ImagePath)) {
    throw "F2 provisioning refused: -ImagePath is required (no image is downloaded)."
}
if (-not (Test-Path -LiteralPath $ImagePath)) {
    throw "F2 provisioning refused: image not found: $ImagePath"
}
if ($GuestIp -eq $HostGatewayIp) {
    throw "F2 provisioning refused: host and guest addresses must differ."
}

# --- Internal switch --------------------------------------------------------
$switch = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if (-not $switch) {
    Write-Step "Creating INTERNAL switch '$SwitchName'"
    New-VMSwitch -Name $SwitchName -SwitchType Internal | Out-Null
} else {
    if ($switch.SwitchType -ne "Internal") {
        throw "F2 provisioning refused: switch '$SwitchName' exists but is $($switch.SwitchType), not Internal."
    }
    Write-Step "Internal switch '$SwitchName' already exists"
}

# Host vNIC address on the switch (no default gateway => no external route).
$hostIf = Get-NetAdapter -Name "vEthernet ($SwitchName)" -ErrorAction SilentlyContinue
if ($hostIf) {
    if (-not (Get-NetIPAddress -InterfaceIndex $hostIf.ifIndex -IPAddress $HostGatewayIp -ErrorAction SilentlyContinue)) {
        Write-Step "Assigning host gateway $HostGatewayIp/$PrefixLength (no default gateway)"
        New-NetIPAddress -InterfaceIndex $hostIf.ifIndex -IPAddress $HostGatewayIp -PrefixLength ([int]$PrefixLength) | Out-Null
    } else {
        Write-Step "Host gateway address already present"
    }
} else {
    Write-Plan "Host vEthernet adapter not found yet; it appears after switch creation. Re-run."
}

# --- VM ---------------------------------------------------------------------
$vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
if (-not $vm) {
    Write-Step "Creating Generation-2 VM '$VmName'"
    New-VM -Name $VmName -Generation 2 -MemoryStartupBytes $MemoryStartupBytes -SwitchName $SwitchName | Out-Null
    Set-VMProcessor -VMName $VmName -Count $ProcessorCount
    Set-VMMemory -VMName $VmName -DynamicMemoryEnabled $false
    # No integration services that expose host content/devices.
    Disable-VMIntegrationService -VMName $VmName -Name "Guest Service Interface" -ErrorAction SilentlyContinue
    Add-VMNetworkAdapter -VMName $VmName -Name "F2NIC" -SwitchName $SwitchName
    if ($ImagePath -like "*.vhdx") {
        Add-VMHardDiskDrive -VMName $VmName -Path $ImagePath
    } else {
        Write-Plan "Non-VHDX image supplied ($ImagePath); attach/install manually, then detach the ISO."
    }
} else {
    Write-Step "VM '$VmName' already exists; leaving definition as-is"
}

# --- Default-deny network policy (extended ACLs) ----------------------------
# Allow the single required flow FIRST (higher weight), then deny everything
# else with a catch-all. Requires the VM to be stopped for ACL changes.
$vmState = (Get-VM -Name $VmName).State
if ($vmState -ne "Off") {
    Write-Plan "VM is $vmState; stop it to apply ACLs, then re-run."
} else {
    # Remove any prior rules for idempotency.
    Get-VMNetworkAdapter -VMName $VmName | Get-VMNetworkAdapterExtendedAcl -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-VMNetworkAdapterExtendedAcl -VMName $VmName -Weight $_.Weight -ErrorAction SilentlyContinue }
    Write-Step "Adding ACL: ALLOW guest->gateway TCP $GatewayPort (weight 100)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Outbound -Action Allow `
        -LocalIPAddress $GuestIp -RemoteIPAddress $HostGatewayIp -RemotePort $GatewayPort -Protocol TCP -Weight 100
    Write-Step "Adding ACL: DENY all else (weight 1)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Outbound -Action Deny -Weight 1
    Write-Step "Adding ACL: DENY inbound (weight 1)"
    Add-VMNetworkAdapterExtendedAcl -VMName $VmName -Direction Inbound -Action Deny -Weight 1
}

Write-Host ""
Write-Host "F2 isolation boundary provisioned (or already present)."
Write-Host "NEXT (authorized steps):"
Write-Host "  1. Prepare the guest golden image (P2 venv + git + node + task toolchain)."
Write-Host "  2. Start the model gateway on the host:"
Write-Host "     `$env:F2_GATEWAY_BIND_HOST='$HostGatewayIp'; `$env:F2_GATEWAY_BIND_PORT='$GatewayPort';"
Write-Host "     `$env:F2_GATEWAY_ALLOWED_CLIENT='$GuestIp'; `$env:F2_MODEL_UPSTREAM='$UpstreamBaseUrl';"
Write-Host "     python -m qwen_train.f2_model_gateway"
Write-Host "  3. In the guest, set SWARM_F2_ISOLATION=1 and run P2 via the F2 worker."
Write-Host "  4. Verify with qwen_train/f2_isolation_preflight.py."
