# UNTESTED - NOT EXECUTED BY THE AGENT.
# =============================================================================
# Experiment J / F2 -- Q9 egress PROBES, run AS the F2 account
# =============================================================================
# Run this AFTER provision.ps1. It prompts (via Get-Credential) for the F2Arm
# password - TYPED BY THE OPERATOR - and launches an inner probe script under that
# account with Start-Process -Credential. The password is never printed, saved,
# or written to a file by this script.
#
# Probes (expected):
#   * internet by IP   (1.1.1.1:443)        -> EXPECT FAIL (blocked)
#   * internet by name (example.com)        -> EXPECT FAIL (blocked)
#   * loopback 127.0.0.1:6333 (Qdrant)      -> NOT blocked
#   * loopback model port 127.0.0.1:8079    -> NOT blocked
#   * positive loopback self-test (127.0.0.1:18271) -> EXPECT SUCCESS
#   * whoami / whoami /groups
#   * python --version (the interpreter F2 will use)
#   * python -m qwen_train.f2_isolation
#
# Outputs go to <OutDir>\probes_<timestamp>.txt (written by the F2Arm process).
# Supports -WhatIf.
# =============================================================================

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$F2AccountName = 'F2Arm',
    [string]$OutDir        = 'C:\Users\rober\F2_EVIDENCE\q9_probes',
    [string]$RepoRoot      = 'C:\Users\rober\Projects\v-horseshoe-v2',
    [string]$Python        = 'C:\Users\rober\Projects\v-horseshoe-v2\.venv\Scripts\python.exe',
    [string]$ProbeShell    = '',
    [string]$EnforcementScope = 'windows_account'
)

$ErrorActionPreference = 'Stop'
function Write-Plan([string]$msg) { Write-Host ("  [plan] " + $msg) }

# The Microsoft Store pwsh (C:\Program Files\WindowsApps\...) cannot be launched
# by another user (WindowsApps denies traversal) -> Start-Process -Credential
# gives "Access is denied". Use a normal shell the F2 account can execute.
if (-not $ProbeShell) {
    $ProbeShell = @(
        'C:\Program Files\PowerShell\7\pwsh.exe',
        'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $ProbeShell -or -not (Test-Path $ProbeShell)) {
    throw 'No runnable probe shell found (expected PowerShell 7 MSI or Windows PowerShell 5.1).'
}

$innerScript = Join-Path $OutDir 'probe_inner.ps1'

$innerBody = @'
param([string]$OutDir, [string]$RepoRoot, [string]$Python, [string]$EnforcementScope)
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$out = Join-Path $OutDir ("probes_" + $stamp + ".txt")
$lines = New-Object System.Collections.Generic.List[string]
function L($s){ $lines.Add($s); Write-Host $s }
function TcpTest($target, $port, $timeoutMs){
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($target, $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($timeoutMs)
        $conn = $ok -and $c.Connected
        if ($conn) { try { $c.EndConnect($iar) } catch {} }
        $c.Close()
        return $conn
    } catch { return $false }
}
L ("F2 Q9 PROBE REPORT  " + (Get-Date -Format o))
L ("whoami: " + (whoami))
L ("--- whoami /groups ---")
(whoami /groups) | ForEach-Object { L $_ }
L ("--- python --version (" + $Python + ") ---")
try { L ((& $Python --version 2>&1) -join ' ') } catch { L ("python FAILED: " + $_.Exception.Message) }
L "--- PROBES ---"
$ipOk   = TcpTest '1.1.1.1' 443 6000
L ("internet-by-IP   1.1.1.1:443      connected=" + $ipOk + "  EXPECTED=False")
$dnsOk = $false
try { $dns = Resolve-DnsName example.com -ErrorAction Stop; $dnsOk = $true } catch { $dnsOk = $false }
L ("internet-by-name DNS example.com  resolved=" + $dnsOk + "  EXPECTED=False")
$nameOk = TcpTest 'example.com' 443 6000
L ("internet-by-name example.com:443 connected=" + $nameOk + "  EXPECTED=False")
$q6333 = TcpTest '127.0.0.1' 6333 4000
L ("loopback         127.0.0.1:6333   connected=" + $q6333 + "  EXPECTED=not-blocked (refused ok)")
$m8079 = TcpTest '127.0.0.1' 8079 4000
L ("loopback-model   127.0.0.1:8079   connected=" + $m8079 + "  EXPECTED=not-blocked (refused ok)")
$selfOk = $false
try {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 18271)
    $listener.Start()
    $selfOk = TcpTest '127.0.0.1' 18271 3000
    $listener.Stop()
} catch { $selfOk = $false }
L ("loopback-self    127.0.0.1:18271  connected=" + $selfOk + "  EXPECTED=True")
L "--- python -m qwen_train.f2_isolation ---"
# Correct invocation: --out is REQUIRED; assess_enforcement_identity() also
# requires --enforcement-scope (recognised), --interpreter-path and
# --interpreter-sha256. External targets must be denied; local services must stay
# reachable. (Argument names taken from qwen_train/f2_isolation.py main().)
try {
    Push-Location $RepoRoot
    $isoOut = Join-Path $OutDir ("isolation_" + $stamp + ".json")
    $pySha  = (Get-FileHash -Algorithm SHA256 -LiteralPath $Python).Hash.ToLower()
    $isoArgs = @(
        '-m', 'qwen_train.f2_isolation',
        '--out', $isoOut,
        '--enforcement-scope', $EnforcementScope,
        '--executing-user', (whoami),
        '--interpreter-path', $Python,
        '--interpreter-sha256', $pySha,
        '--spawn-image', $Python,
        '--external', 'api.github.com:443',
        '--required-service', 'qdrant=127.0.0.1:6333',
        '--required-service', 'model=127.0.0.1:8079'
    )
    $iso = (& $Python @isoArgs 2>&1) -join "`n"
    L $iso
    L ("attestation path: " + $isoOut)
} catch { L ("f2_isolation FAILED: " + $_.Exception.Message) } finally { Pop-Location }
L ("END " + (Get-Date -Format o))
[IO.File]::WriteAllLines($out, $lines)
Write-Host ("WROTE " + $out)
'@

if (-not $PSCmdlet.ShouldProcess($F2AccountName, 'Run Q9 probes as the F2 account')) {
    Write-Plan ("write inner probe script to {0}" -f $innerScript)
    Write-Plan ("Get-Credential {0} (operator types the password)" -f $F2AccountName)
    Write-Plan ("Start-Process {0} -Credential <cred> -WorkingDirectory {1} -File {2} -Wait" -f $ProbeShell, $OutDir, $innerScript)
    Write-Plan ("results -> {0}\probes_<timestamp>.txt" -f $OutDir)
    return
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Set-Content -LiteralPath $innerScript -Value $innerBody -Encoding utf8

$cred = Get-Credential -UserName $F2AccountName -Message "Enter the password for the F2 account '$F2AccountName' (never stored)"
$p = Start-Process -FilePath $ProbeShell `
    -Credential $cred `
    -WorkingDirectory $OutDir `
    -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$innerScript,'-OutDir',$OutDir,'-RepoRoot',$RepoRoot,'-Python',$Python,'-EnforcementScope',$EnforcementScope) `
    -Wait -PassThru
Write-Host ("Probe process exit code: " + $p.ExitCode)
Write-Host ("Results are under: " + $OutDir)
