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

  ROOT CAUSE RECORDED BY BOUNDED PROBE (2026-10-10, F2-S8-NAT / F2-S8-VM1):
  a Stateful $true entry is REJECTED with 0x80070057 unless an explicit
  -Protocol is supplied.  P5 (stateful, no protocol) and P7 (stateful, no
  protocol) failed; P6 (stateful + -Protocol TCP) and P9 (stateful + TCP, no
  idle) succeeded.  P1/P2/P4 proved ANY, CIDR, and CIDR-local+CIDR-remote all
  apply, and that every probe could be removed leaving zero residue.  P3 proved
  the "a-b" range syntax is NOT supported.  The policy below therefore gives
  every stateful allow an explicit protocol, and uses no range syntax.

  Official semantics relied on (Microsoft Learn,
  Add-VMNetworkAdapterExtendedAcl): "Larger weight values apply first, and once
  an ACL entry applies to a packet, other entries are no longer relevant for
  that packet"; addresses accept a host or subnet address, 0.0.0.0/0, ::/0, or
  ANY for all IPv4 and IPv6 addresses; Stateful $True makes an entry apply to
  the return packet of the session.

.NOTES
  Approval to apply is a separate, explicit operator decision.  Passing -Apply
  is the operator's authorization; nothing in this file authorizes itself.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$VMName = 'F2-S8-VM1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 11-rule IPv4 policy, approved by the operator.  Larger weight applies first.
#   #1/#2 must outrank #3 because the NAT gateway 10.73.0.1 lives inside 10.0.0.0/8.
#   #8/#9 are IPv4-only on purpose: `ANY` would also cover IPv6, which must fall
#   through to the ANY default deny at #10 rather than be permitted.
#   Every stateful entry carries -Protocol, which this host requires.
#   Local is ANY because the ACL is already bound to this vNIC.
$Rules = @(
    @{ N = 1;  Direction = 'Outbound'; Action = 'Allow'; Local = 'ANY'; Remote = '10.73.0.1';   Protocol = 'TCP'; Weight = 900; Stateful = $true  }
    @{ N = 2;  Direction = 'Outbound'; Action = 'Allow'; Local = 'ANY'; Remote = '10.73.0.1';   Protocol = 'UDP'; Weight = 901; Stateful = $true  }
    @{ N = 3;  Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = '10.0.0.0/8';  Protocol = '';    Weight = 800; Stateful = $false }
    @{ N = 4;  Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = '192.168.0.0/16'; Protocol = ''; Weight = 700; Stateful = $false }
    @{ N = 5;  Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = '172.16.0.0/12';  Protocol = ''; Weight = 600; Stateful = $false }
    @{ N = 6;  Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = '169.254.0.0/16'; Protocol = ''; Weight = 500; Stateful = $false }
    @{ N = 7;  Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = '10.72.0.0/24';   Protocol = ''; Weight = 400; Stateful = $false }
    @{ N = 8;  Direction = 'Outbound'; Action = 'Allow'; Local = 'ANY'; Remote = '0.0.0.0/0';  Protocol = 'TCP'; Weight = 300; Stateful = $true  }
    @{ N = 9;  Direction = 'Outbound'; Action = 'Allow'; Local = 'ANY'; Remote = '0.0.0.0/0';  Protocol = 'UDP'; Weight = 301; Stateful = $true  }
    @{ N = 10; Direction = 'Outbound'; Action = 'Deny';  Local = 'ANY'; Remote = 'ANY';        Protocol = '';    Weight = 1;   Stateful = $false }
    @{ N = 11; Direction = 'Inbound';  Action = 'Deny';  Local = 'ANY'; Remote = 'ANY';        Protocol = '';    Weight = 1;   Stateful = $false }
)

function Get-RuleParams($r) {
    $p = @{
        VMName          = $VMName
        Direction       = $r.Direction
        Action          = $r.Action
        LocalIPAddress  = $r.Local
        RemoteIPAddress = $r.Remote
        Weight          = $r.Weight
        Stateful        = $r.Stateful
    }
    if ($r.Protocol) { $p['Protocol'] = $r.Protocol }
    if ($r.Stateful) { $p['IdleSessionTimeout'] = 1800 }
    return $p
}

Write-Output "F2-S8-VM1 extended-ACL plan  ($($Rules.Count) rules)"
$Rules | ForEach-Object {
    Write-Output ("  {0,2}. {1,-8} {2,-5} {3,-6} -> {4,-15} proto={5,-4} w={6,-4} stateful={7}" -f
        $_.N, $_.Direction, $_.Action, $_.Local, $_.Remote, $(if ($_.Protocol) { $_.Protocol } else { '-' }), $_.Weight, $_.Stateful)
}

if (-not $Apply) {
    Write-Output ''
    Write-Output 'DRY RUN - nothing was changed. Exact cmdlets that WOULD run:'
    $Rules | ForEach-Object {
        $p = Get-RuleParams $_
        $bits = ($p.GetEnumerator() | Sort-Object Name | ForEach-Object { "-$($_.Key) '$($_.Value)'" }) -join ' '
        Write-Output ("  Add-VMNetworkAdapterExtendedAcl $bits")
    }
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
    $params = Get-RuleParams $r
    Add-VMNetworkAdapterExtendedAcl @params
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
        else {
            if ([bool]$hit.Stateful -ne [bool]$r.Stateful) {
                $mismatches += "rule $($r.N) stateful mismatch: read-back $($hit.Stateful), intended $($r.Stateful)"
            }
            $rp = if ($r.Protocol) { $r.Protocol } else { 'ANY' }
            if (("$($hit.Protocol)") -ne $rp) {
                $mismatches += "rule $($r.N) protocol mismatch: read-back $($hit.Protocol), intended $rp"
            }
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
