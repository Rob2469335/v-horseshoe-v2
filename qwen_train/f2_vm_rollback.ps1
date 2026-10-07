<#
.SYNOPSIS
  Remove ONLY the dedicated F2 isolation objects (safety rollback artifact).

.DESCRIPTION
  Removes, in safe order, exactly: the F2 VM, the F2 Internal switch, and the
  10.72.0.1/24 address from the F2 host adapter. It refuses to operate on any
  object whose name does not EXACTLY match the authorized F2 names, refuses
  broad/wildcard deletion, supports -WhatIf, and prompts for confirmation.

  This file is a safety artifact. It is NOT run automatically and must never be
  invoked by an agent without explicit operator authorization.

.EXAMPLE
  ./qwen_train/f2_vm_rollback.ps1 -WhatIf
.EXAMPLE
  ./qwen_train/f2_vm_rollback.ps1 -Force
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [switch]$Force,
    [string]$VmName = "F2-Isolation-VM",
    [string]$SwitchName = "F2-Internal-Switch",
    [string]$HostGatewayIp = "10.72.0.1",
    [string]$PrefixLength = "24"
)

# Authorized identities — anything else is refused.
$AUTH_VM = "F2-Isolation-VM"
$AUTH_SWITCH = "F2-Internal-Switch"

$ErrorActionPreference = "Stop"

if ($VmName -ne $AUTH_VM) { throw "refused: VM name '$VmName' is not the authorized F2 VM '$AUTH_VM'." }
if ($SwitchName -ne $AUTH_SWITCH) { throw "refused: switch name '$SwitchName' is not the authorized F2 switch '$AUTH_SWITCH'." }
if ($HostGatewayIp -ne "10.72.0.1") { throw "refused: address '$HostGatewayIp' is not the authorized F2 host IP." }

if (-not $Force) {
    $ans = Read-Host "About to REMOVE the F2 VM '$VmName', switch '$SwitchName' and IP 10.72.0.1. Type 'yes' to confirm"
    if ($ans -ne "yes") { Write-Host "aborted."; return }
}

# 1) VM (exact match only)
$vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
if ($vm) {
    if ($vm.Name -ne $AUTH_VM) { throw "refused: unexpected VM identity '$($vm.Name)'." }
    if ($PSCmdlet.ShouldProcess($vm.Name, "Remove-VM -Force")) {
        if ($vm.State -ne "Off") { throw "refused: VM '$($vm.Name)' is $($vm.State); stop it first (this script never starts/stops)." }
        Remove-VM -Name $vm.Name -Force
        Write-Host "removed VM $($vm.Name)"
    }
} else {
    Write-Host "no VM named '$VmName' (nothing to remove)"
}

# 2) Host IP on the F2 vEthernet (never touch other adapters/IPs)
$ifAlias = "vEthernet ($SwitchName)"
$nic = Get-NetAdapter -Name $ifAlias -ErrorAction SilentlyContinue
if ($nic) {
    $ip = Get-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $HostGatewayIp -ErrorAction SilentlyContinue
    if ($ip) {
        if ($PSCmdlet.ShouldProcess($HostGatewayIp, "Remove-NetIPAddress")) {
            Remove-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $HostGatewayIp -PrefixLength ([int]$PrefixLength) -Confirm:$false
            Write-Host "removed IP $HostGatewayIp from $ifAlias"
        }
    } else {
        Write-Host "no $HostGatewayIp on $ifAlias (nothing to remove)"
    }
} else {
    Write-Host "no adapter '$ifAlias' (nothing to remove)"
}

# 3) Switch (exact match only; Internal expected)
$sw = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if ($sw) {
    if ($sw.Name -ne $AUTH_SWITCH) { throw "refused: unexpected switch identity '$($sw.Name)'." }
    if ($PSCmdlet.ShouldProcess($sw.Name, "Remove-VMSwitch")) {
        Remove-VMSwitch -Name $sw.Name -Force
        Write-Host "removed switch $($sw.Name)"
    }
} else {
    Write-Host "no switch named '$SwitchName' (nothing to remove)"
}

Write-Host "F2 rollback complete (VM, host IP, switch)."
