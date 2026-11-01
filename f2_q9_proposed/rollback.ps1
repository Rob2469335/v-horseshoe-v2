# UNTESTED - NOT EXECUTED BY THE AGENT.
# =============================================================================
# Experiment J / F2 -- Q9 PROVISIONING rollback (idempotent, half-done safe)
# =============================================================================
# Removes ONLY what provision.ps1 creates:
#   * firewall rules whose DisplayName begins 'F2-Q9-';
#   * the traverse-only ACEs on the profile/projects parents;
#   * the filesystem ACL entries provision.ps1 granted to the F2 account;
#   * the dedicated F2 account, and ONLY on an explicit -RemoveAccount request.
#
# HALF-DONE SAFE: handles the state left when provision.ps1 failed after creating
# the account but before creating rules/ACLs. Every step is a no-op when its
# target is absent. It NEVER deletes an account merely because a name matches:
# -RemoveAccount is required, and it refuses to remove the account in use.
# Supports -WhatIf.
# =============================================================================

#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$F2AccountName = 'F2Arm',
    [string]$RepoRoot     = 'C:\Users\rober\Projects\v-horseshoe-v2',
    [string]$ModelsRoot   = 'C:\Users\rober\models',
    [string]$DataRoot     = 'C:\Users\rober\Projects\qwen_train_data',
    [string]$EvidenceRoot = 'C:\Users\rober\F2_EVIDENCE',
    [string]$ProfileRoot  = 'C:\Users\rober',
    [string]$ProjectsRoot = 'C:\Users\rober\Projects',
    [switch]$RemoveAccount
)

$ErrorActionPreference = 'Stop'
function Write-Plan([string]$msg) { Write-Host ("  [plan] " + $msg) }

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This rollback must be run by an Administrator.'
}

# --- 0. Report current (possibly half-done) state ----------------------------
$acct = Get-LocalUser -Name $F2AccountName -ErrorAction SilentlyContinue
$rules = Get-NetFirewallRule -DisplayName 'F2-Q9-*' -ErrorAction SilentlyContinue
$aclHits = 0
foreach ($p in @($RepoRoot, $ModelsRoot, $DataRoot, $EvidenceRoot, $ProfileRoot, $ProjectsRoot)) {
    if (Test-Path -LiteralPath $p) { if (icacls $p 2>&1 | Select-String -SimpleMatch $F2AccountName) { $aclHits++ } }
}
Write-Host ("State: account={0}  F2-Q9-* rules={1}  ACL-granted paths={2}" -f `
    $(if ($acct) { 'present' } else { 'absent' }), (($rules | Measure-Object).Count), $aclHits)

# --- 1. Remove ONLY the F2-Q9-* firewall rules -------------------------------
if ($rules) {
    foreach ($r in $rules) {
        if ($PSCmdlet.ShouldProcess($r.DisplayName, 'Remove firewall rule')) { Remove-NetFirewallRule -Name $r.Name }
        else { Write-Plan ("remove firewall rule '{0}'" -f $r.DisplayName) }
    }
} else {
    Write-Host 'No F2-Q9-* firewall rules found (nothing to remove).'
}

# --- 2. Remove the traverse-only ACEs (non-recursive, folder only) -----------
foreach ($p in @($ProfileRoot, $ProjectsRoot)) {
    if (-not (Test-Path -LiteralPath $p)) { continue }
    if ($PSCmdlet.ShouldProcess($p, "Remove traverse ACE for $F2AccountName")) {
        & icacls $p /remove:g $F2AccountName /C /Q | Out-Null
    } else {
        Write-Plan ("icacls `"{0}`" /remove:g {1}" -f $p, $F2AccountName)
    }
}

# --- 3. Remove the recursive ACL grants --------------------------------------
foreach ($p in @($RepoRoot, $ModelsRoot, $DataRoot, $EvidenceRoot)) {
    if (-not (Test-Path -LiteralPath $p)) { continue }
    if ($PSCmdlet.ShouldProcess($p, "Remove ACL entry for $F2AccountName")) {
        & icacls $p /remove:g $F2AccountName /T /C /Q | Out-Null
    } else {
        Write-Plan ("icacls `"{0}`" /remove:g {1} /T" -f $p, $F2AccountName)
    }
}

# --- 4. Remove the dedicated account ONLY on explicit, checked request --------
if (-not $RemoveAccount) {
    Write-Host ("Account '{0}' left in place. Re-run with -RemoveAccount to remove it." -f $F2AccountName)
    return
}
if (-not $acct) { Write-Host ("Account '{0}' does not exist; nothing to remove." -f $F2AccountName); return }
if ($env:USERNAME -eq $F2AccountName) { throw "Refusing to remove '$F2AccountName': it is the account currently in use." }
if ($PSCmdlet.ShouldProcess($F2AccountName, 'Remove local account')) {
    Remove-LocalUser -Name $F2AccountName
    Write-Host ("Removed local account '{0}'." -f $F2AccountName)
} else {
    Write-Plan ("remove local account '{0}'" -f $F2AccountName)
}
