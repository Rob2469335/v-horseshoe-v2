<#
.SYNOPSIS
  Behavioural harness for the S8 ACL comparator (qwen_train/f2_s8_acl.ps1).

.DESCRIPTION
  READ-ONLY. Parses f2_s8_acl.ps1 with the PowerShell AST, extracts the
  operator's $Rules table and the Compare-AclTable function, and drives them
  with fabricated read-back objects. It never calls a Hyper-V cmdlet, never
  reads or writes an ACL, and never reaches the script's mutating path.

  Run it by hand:
    pwsh -NoProfile -NonInteractive -File qwen_train/f2_s8_acl_harness.ps1
  Exit 0 = every property holds; exit 1 = a property failed (named on stdout);
  exit 2 = the script could not be parsed, or the function/table was not found.

  It is deliberately NOT driven from pytest: tests/conftest.py's autouse
  `global_subprocess_mock` guarantees that no subprocess ever starts inside the
  suite, and weakening that guarantee REQUIRES AUTHORIZATION. The pytest suite
  pins this harness's existence and its coverage statically; the behaviour
  itself is checked by running this file.

  Measured 2026-10-14 under PowerShell 7.6.6:
    * PASS against the current f2_s8_acl.ps1;
    * against a copy with the array wrapper re-introduced -> FAILS
      "exact match reports zero mismatches" (the false MISMATCH defect);
    * against a copy with the rule-count check removed -> FAILS
      "an EXTRA rule is reported" (the fail-open defect).
#>
[CmdletBinding()]
param([string]$ScriptPath = (Join-Path $PSScriptRoot 'f2_s8_acl.ps1'))

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path -LiteralPath $ScriptPath).Path, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and $parseErrors.Count -gt 0) {
    Write-Output "PARSE ERROR: $($parseErrors[0].Message)"; exit 2 }

$fn = $ast.Find({ param($n) $n.GetType().Name -eq 'FunctionDefinitionAst' -and $n.Name -eq 'Compare-AclTable' }, $true)
if (-not $fn) { Write-Output 'Compare-AclTable not found'; exit 2 }

$rulesAssign = $ast.Find({ param($n) $n.GetType().Name -eq 'AssignmentStatementAst' -and $n.Left.Extent.Text -eq '$Rules' }, $true)
if (-not $rulesAssign) { Write-Output '$Rules assignment not found'; exit 2 }

Invoke-Expression $rulesAssign.Extent.Text
Invoke-Expression $fn.Extent.Text

function New-Readback {
    param($Rules)
    foreach ($r in $Rules) {
        [pscustomobject]@{
            Direction      = $r.Direction
            Action         = $r.Action
            Weight         = [int]$r.Weight
            RemoteIPAddress = [string]$r.Remote
            Stateful       = [bool]$r.Stateful
            Protocol       = if ($r.Protocol) { [string]$r.Protocol } else { 'ANY' }
        }
    }
}

$fails = 0
function Check([string]$name, [bool]$ok, [string]$detail) {
    if ($ok) { Write-Output "  [ok]   $name" }
    else { Write-Output "  [FAIL] $name -- $detail"; $script:fails++ }
}

$exact = @(New-Readback $Rules)
$m = @(Compare-AclTable $exact)
Check 'exact match reports zero mismatches' ($m.Count -eq 0) `
    "got $($m.Count): $($m -join ' | ')"

$m2 = @(Compare-AclTable @())
Check 'a fully missing rule set is reported per rule plus a count difference' `
    ($m2.Count -eq (@($Rules).Count + 1)) "got $($m2.Count): $($m2 -join ' | ')"

$extraRule = [pscustomobject]@{ Direction = 'Outbound'; Action = 'Allow'
    Weight = 999; RemoteIPAddress = '0.0.0.0/0'; Stateful = $true; Protocol = 'TCP' }
$m3 = @(Compare-AclTable (@($exact) + @($extraRule)))
Check 'an EXTRA rule is reported' (($m3 -join ' ') -match 'rule count differs') `
    "got: $($m3 -join ' | ')"

$flipped = @($exact); $p0 = $flipped[0]
$flipped[0] = [pscustomobject]@{ Direction = $p0.Direction; Action = $p0.Action
    Weight = $p0.Weight; RemoteIPAddress = $p0.RemoteIPAddress
    Stateful = (-not $p0.Stateful); Protocol = $p0.Protocol }
$m4 = @(Compare-AclTable $flipped)
Check 'a stateful flip is reported' (($m4 -join ' ') -match 'stateful mismatch') `
    "got: $($m4 -join ' | ')"

$proto = @($exact); $p1 = $proto[0]
$proto[0] = [pscustomobject]@{ Direction = $p1.Direction; Action = $p1.Action
    Weight = $p1.Weight; RemoteIPAddress = $p1.RemoteIPAddress
    Stateful = $p1.Stateful; Protocol = 'UDP' }
$m5 = @(Compare-AclTable $proto)
Check 'a protocol change is reported' (($m5 -join ' ') -match 'protocol mismatch') `
    "got: $($m5 -join ' | ')"

$heavy = @($exact); $p2 = $heavy[0]
$heavy[0] = [pscustomobject]@{ Direction = $p2.Direction; Action = $p2.Action
    Weight = 5; RemoteIPAddress = $p2.RemoteIPAddress
    Stateful = $p2.Stateful; Protocol = $p2.Protocol }
$m6 = @(Compare-AclTable $heavy)
Check 'a weight change is reported' (($m6 -join ' ') -match 'missing or different') `
    "got: $($m6 -join ' | ')"

if ($fails -gt 0) { Write-Output "FAILURES: $fails"; exit 1 }
Write-Output 'PASS'
exit 0
