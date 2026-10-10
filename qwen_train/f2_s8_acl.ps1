<#
.SYNOPSIS
  Apply and verify the F2-S8-VM1 extended-ACL rule set.

.DESCRIPTION
  Repository-side, non-mutating by default.  Without -Apply it prints the exact
  rule table and the exact cmdlets it would run, and changes nothing.  With
  -Apply it refuses unless the target VM exists and currently has NO extended
  ACLs, applies the table, then reads the ACLs back TWICE and compares both
  read-backs against the table.  It never modifies another VM, never touches a
  switch, NAT, firewall, Avast or any host setting.

  Rule weights follow the precedent already present on F2-Isolation-VM
  (default-deny at a low weight, specific allow at a high weight).  Ordering is
  assumed highest-weight-first, first-match-wins; that assumption is recorded as
  SUPPORTED, not PROVEN, and the read-back plus a live connectivity test confirm
  it.

  Evidence label: rule-set definition and read-back comparison are PROVEN on
  execution; the effective network policy is NOT ESTABLISHED until the live
  connectivity test is run under authorization.

.NOTES
  Approval to apply is a separate, explicit operator decision.  Passing -Apply
  is the operator's authorization; nothing in this file authorizes itself.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$VMName  = 'F2-S8-VM1',
    [string]$GuestIP = '10.73.0.2',
    [string]$Gateway = '10.73.0.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Ordered exactly as tabled and approved for review.  Highest weight = highest
# priority.  #1 must outrank #2 because the NAT gateway lives inside 10.0.0.0/8.
$Rules = @(
    @{ N = 1; Direction = 'Outbound'; Action = 'Allow'; Local = $GuestIP; Remote = $Gateway;          Weight = 900 }
    @{ N = 2; Direction = 'Outbound'; Action = 'Deny';  Local = $GuestIP; Remote = '10.0.0.0/8';     Weight = 800 }
    @{ N = 3; Direction = 'Outbound'; Action = 'Deny';  Local = $GuestIP; Remote = '192.168.0.0/16'; Weight = 700 }
    @{ N = 4; Direction = 'Outbound'; Action = 'Deny';  Local = $GuestIP; Remote = '172.16.0.0/12';  Weight = 600 }
    @{ N = 5; Direction = 'Outbound'; Action = 'Deny';  Local = $GuestIP; Remote = '169.254.0.0/16'; Weight = 500 }
    @{ N = 6; Direction = 'Outbound'; Action = 'Deny';  Local = $GuestIP; Remote = '10.72.0.0/24';   Weight = 400 }
    @{ N = 7; Direction = 'Outbound'; Action = 'Allow'; Local = $GuestIP; Remote = 'ANY';            Weight = 300 }
    @{ N = 8; Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY';    Remote = 'ANY';            Weight = 1   }
    @{ N = 9; Direction = 'Inbound';  Action = 'Deny';  Local = 'ANY';    Remote = 'ANY';            Weight = 1   }
)

function Get-CommandText {
    $Rules | ForEach-Object {
        "Add-VMNetworkAdapterExtendedAcl -VMName '$VMName' -Direction $($_.Direction) " +
        "-Action $($_.Action) -LocalIPAddress '$($_.Local)' -RemoteIPAddress '$($_.Remote)' " +
        "-Weight $($_.Weight) -Stateful `$false"
    }
}

Write-Output "F2-S8-VM1 extended-ACL plan  ($($Rules.Count) rules)"
$Rules | ForEach-Object {
    Write-Output ("  {0}. {1,-8} {2,-5} {3,-11} -> {4,-16} w={5}" -f
        $_.N, $_.Direction, $_.Action, $_.Local, $_.Remote, $_.Weight)
}

if (-not $Apply) {
    Write-Output ''
    Write-Output 'DRY RUN - nothing was changed. Exact cmdlets that WOULD run:'
    Get-CommandText | ForEach-Object { Write-Output ("  $_") }
    Write-Output ''
    Write-Output 'Re-run with -Apply only after the operator has explicitly approved this rule set.'
    exit 0
}

# ---- apply (operator-authorized) ----------------------------------------- #
$vm = Get-VM -Name $VMName -ErrorAction SilentlyContinue
if (-not $vm) { Write-Output "ABORT: VM '$VMName' does not exist."; exit 2 }
$existing = @(Get-VMNetworkAdapterExtendedAcl -VMName $VMName)
if ($existing.Count -gt 0) {
    Write-Output "ABORT: '$VMName' already has $($existing.Count) extended ACL(s); refusing to stack rules."
    exit 2
}

foreach ($r in $Rules) {
    Add-VMNetworkAdapterExtendedAcl -VMName $VMName -Direction $r.Direction -Action $r.Action `
        -LocalIPAddress $r.Local -RemoteIPAddress $r.Remote -Weight $r.Weight -Stateful $false
}
Write-Output 'Applied. Reading back twice for comparison...'

function Compare-AclTable($readback) {
    $mismatches = @()
    foreach ($r in $Rules) {
        $hit = $readback | Where-Object {
            $_.Direction -eq $r.Direction -and $_.Action -eq $r.Action -and
            [int]$_.Weight -eq [int]$r.Weight -and
            ("$($_.RemoteIPAddress)") -eq $r.Remote
        }
        if (-not $hit) {
            $mismatches += "rule $($r.N) missing or different: $($r.Direction) $($r.Action) -> $($r.Remote) w=$($r.Weight)"
        }
    }
    return $mismatches
}

$rb1 = @(Get-VMNetworkAdapterExtendedAcl -VMName $VMName)
$rb2 = @(Get-VMNetworkAdapterExtendedAcl -VMName $VMName)
$m1 = Compare-AclTable $rb1
$m2 = Compare-AclTable $rb2

Write-Output "read-back 1: $($rb1.Count) entries, mismatches: $($m1.Count)"
Write-Output "read-back 2: $($rb2.Count) entries, mismatches: $($m2.Count)"
$m1 | ForEach-Object { Write-Output "  ! $_" }
$m2 | ForEach-Object { Write-Output "  ! $_" }
if ($m1.Count -gt 0 -or $m2.Count -gt 0) {
    Write-Output 'RESULT: MISMATCH - effective rule set does not match the intended policy. Stop.'
    exit 3
}
Write-Output 'RESULT: rule set matches the intended policy in both read-backs.'
Write-Output 'NOTE: effective network policy is NOT ESTABLISHED until the authorized connectivity test passes.'
exit 0
