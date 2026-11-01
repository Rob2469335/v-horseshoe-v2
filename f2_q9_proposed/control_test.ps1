# UNTESTED - NOT EXECUTED BY THE AGENT.
# =============================================================================
# Experiment J / F2 -- Q9 CONTROL TEST (isolate pwsh-path vs account/logon)
# =============================================================================
# Same Get-Credential + Start-Process -Credential pattern as run_probes_as_f2.ps1,
# but launches C:\Windows\System32\cmd.exe /c whoami with the working directory
# C:\Windows\System32 (both readable by any user).
#   * If this SUCCEEDS -> the account/logon is fine; the earlier failure was the
#     Microsoft Store pwsh path (WindowsApps).
#   * If this FAILS  -> the problem is the account or logon rights.
# The password is typed by the operator and never stored. Supports -WhatIf.
# =============================================================================

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$F2AccountName = 'F2Arm',
    [string]$OutFile       = 'C:\Users\rober\F2_EVIDENCE\q9_probes\control_whoami.txt'
)

$ErrorActionPreference = 'Stop'
$exe = 'C:\Windows\System32\cmd.exe'

if (-not $PSCmdlet.ShouldProcess($F2AccountName, 'Run control test (cmd /c whoami)')) {
    Write-Host ("  [plan] Get-Credential {0} (operator types the password)" -f $F2AccountName)
    Write-Host ("  [plan] Start-Process {0} -Credential <cred> -WorkingDirectory C:\Windows\System32 -ArgumentList '/c', 'whoami > `"{1}`" 2>&1'" -f $exe, $OutFile)
    Write-Host ("  [plan] read {0}" -f $OutFile)
    return
}

$cred = Get-Credential -UserName $F2AccountName -Message "Control test: enter the password for '$F2AccountName' (never stored)"
$p = Start-Process -FilePath $exe `
    -Credential $cred `
    -WorkingDirectory 'C:\Windows\System32' `
    -ArgumentList '/c', ("whoami > `"{0}`" 2>&1 & echo EXIT=%errorlevel% >> `"{0}`"" -f $OutFile) `
    -Wait -PassThru
Write-Host ("Control process exit code: " + $p.ExitCode)
if (Test-Path $OutFile) { Write-Host "--- control output ---"; Get-Content $OutFile } else { Write-Host "no output file produced" }
