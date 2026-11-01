# UNTESTED - NOT EXECUTED BY THE AGENT.
# =============================================================================
# Experiment J / F2 -- Q9 execution-identity + egress-enforcement PROVISIONING
# =============================================================================
# Operator-authorized Q9 design (Rob):
#   * execution identity = windows_account: a dedicated LOCAL, NON-ADMIN account;
#   * egress enforcement = identity-scoped firewall BLOCK rules named F2-Q9-*;
#   * NO machine-wide firewall change.
#
# IDEMPOTENT: safe to re-run after a partial failure. If the account already
# exists it is NOT recreated and NO password is requested. Existing F2-Q9-* rules
# are updated in place; existing ACL grants are skipped.
#
# -LocalUser format: a String parameter that expects the user SDDL, NOT a bare
# SID. Verified on this host: `-LocalUser 'D:(A;;CC;;;<SID>)'` is accepted and
# `Get-NetFirewallSecurityFilter` stores it naming the account SID.
# -Enabled format: the parameter is the NetSecurity Enabled ENUM, not a Boolean.
# Verified on this host: `-Enabled 'True'` works; `-Enabled $true` fails with
# "Invalid cast from System.Boolean".
#
# NO silent fallback: if the SDDL form is rejected the script STOPS with the
# exact error (a wrong rule must never be created quietly).
#
# Platform facts (qwen_train/f2_isolation.py PLATFORM_LIMITS, from Microsoft docs):
#   1. Windows Firewall does NOT filter loopback.
#   2. A -Program rule does NOT inherit to child processes; scoping by ACCOUNT
#      (-LocalUser) covers every descendant of the arm.
#
# This script contains NO password and NO automated logon mechanism. It supports
# -WhatIf (dry run).
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
    [string]$ProjectsRoot = 'C:\Users\rober\Projects'
)

$ErrorActionPreference = 'Stop'
function Write-Plan([string]$msg) { Write-Host ("  [plan] " + $msg) }

# --- 0. Preconditions --------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This provisioning must be run by an Administrator.'
}

# --- 1. Dedicated LOCAL, NON-ADMIN account (idempotent) ----------------------
$acct = Get-LocalUser -Name $F2AccountName -ErrorAction SilentlyContinue
if ($acct) {
    Write-Host "Account '$F2AccountName' already exists (SID $($acct.SID.Value)); skipping creation (no password requested)."
    $sidValue = $acct.SID.Value
} else {
    $securePw = $null
    if (-not $WhatIfPreference) {
        $securePw = Read-Host -AsSecureString "Enter a password for the new F2 account '$F2AccountName' (never stored)"
        if ($null -eq $securePw -or $securePw.Length -eq 0) {
            throw 'Refusing to create the account with an empty password.'
        }
    }
    if ($PSCmdlet.ShouldProcess($F2AccountName, 'Create local NON-admin account')) {
        New-LocalUser -Name $F2AccountName `
            -Password $securePw -AccountNeverExpires `
            -Description 'Experiment J F2 arm execution identity (Q9)' | Out-Null
        $securePw = $null
        $sidValue = (Get-LocalUser -Name $F2AccountName).SID.Value
    } else {
        Write-Plan "create local user '$F2AccountName' (non-admin, never-expires)"
        $sidValue = '<new-account-sid>'
    }
}

if ($sidValue -ne '<new-account-sid>') {
    $admins = (Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue).Name
    if ($admins -contains "$env:COMPUTERNAME\$F2AccountName") {
        throw "Refusing to continue: '$F2AccountName' is unexpectedly an Administrator."
    }
}
$localUserSddl = "D:(A;;CC;;;$sidValue)"
Write-Host ("F2 account: {0}  SID: {1}" -f $F2AccountName, $sidValue)

# --- 2. Identity-scoped, NON-LOCAL outbound BLOCK, IPv4 + IPv6 (idempotent) ---
$nonLocalV4 = @('0.0.0.0-126.255.255.255', '128.0.0.0-255.255.255.255')
$nonLocalV6 = @('2000::/3', 'fc00::/7', 'fe80::/10')  # non-loopback IPv6; ::1 excluded

$rules = @(
    @{ Name = 'F2-Q9-outbound-nonlocal-deny-v4'; Addr = $nonLocalV4 },
    @{ Name = 'F2-Q9-outbound-nonlocal-deny-v6'; Addr = $nonLocalV6 }
)

foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) {
        if ($PSCmdlet.ShouldProcess($r.Name, 'Update existing F2-Q9 rule in place')) {
            Set-NetFirewallRule -DisplayName $r.Name -Action Block -Direction Outbound -Enabled 'True' -Profile Any | Out-Null
            # The Set-NetFirewall*Filter cmdlets bind a FILTER object, not the rule
            # object: pipe Get-NetFirewallAddressFilter / -SecurityFilter output.
            ($existing | Get-NetFirewallAddressFilter) | Set-NetFirewallAddressFilter -RemoteAddress $r.Addr | Out-Null
            ($existing | Get-NetFirewallSecurityFilter) | Set-NetFirewallSecurityFilter -LocalUser $localUserSddl | Out-Null
            Write-Host ("Updated existing rule '{0}'." -f $r.Name)
        } else {
            Write-Plan ("update existing rule '{0}' in place (LocalUser={1}, RemoteAddress={2})" -f $r.Name, $localUserSddl, ($r.Addr -join ','))
        }
        continue
    }
    if ($PSCmdlet.ShouldProcess($r.Name, "Create outbound BLOCK for $F2AccountName")) {
        # NO fallback: if the SDDL form is rejected, this throws with the exact error.
        New-NetFirewallRule `
            -DisplayName $r.Name `
            -Description 'Experiment J F2: default-deny outbound to non-local destinations for the F2 arm identity' `
            -Direction Outbound -Action Block -Profile Any `
            -Enabled 'True' `
            -LocalUser $localUserSddl -RemoteAddress $r.Addr | Out-Null
        Write-Host ("Created rule '{0}'." -f $r.Name)
    } else {
        Write-Plan ("create outbound BLOCK rule '{0}' LocalUser={1} RemoteAddress={2} Enabled=True" -f $r.Name, $localUserSddl, ($r.Addr -join ','))
    }
}

# --- 3. Traverse-only grants (reach the F2 paths; NOT read of the profile) ----
# F2Arm has no access to C:\Users\rober or C:\Users\rober\Projects by default.
# Traverse-only (X) lets it reach the granted subpaths without listing the parent.
$traverse = @($ProfileRoot, $ProjectsRoot)
foreach ($p in $traverse) {
    $already = (icacls $p 2>&1 | Select-String -SimpleMatch $F2AccountName)
    if ($already) { Write-Host ("Traverse already granted to '{0}' on {1}; skipping." -f $F2AccountName, $p); continue }
    if ($PSCmdlet.ShouldProcess($p, "Grant traverse-only (X) to $F2AccountName")) {
        & icacls $p /grant "$F2AccountName`:(X)" | Out-Null
    } else {
        Write-Plan ("icacls `"{0}`" /grant {1}:(X)" -f $p, $F2AccountName)
    }
}

# --- 4. Least-privilege filesystem grants (idempotent) -----------------------
$grants = @(
    @{ Path = $RepoRoot;     Rights = 'RX'; Recurse = $true  },  # read + execute the code
    @{ Path = $ModelsRoot;   Rights = 'R';  Recurse = $true  },  # READ ONLY
    @{ Path = $DataRoot;     Rights = 'R';  Recurse = $true  },  # READ ONLY
    @{ Path = $EvidenceRoot; Rights = 'M';  Recurse = $true  }   # F2 writes evidence
)
foreach ($g in $grants) {
    $spec = "$F2AccountName`:(OI)(CI)$($g.Rights)"
    $already = (icacls $g.Path 2>&1 | Select-String -SimpleMatch $F2AccountName)
    if ($already) { Write-Host ("ACL already grants '{0}' on {1}; skipping." -f $F2AccountName, $g.Path); continue }
    if ($PSCmdlet.ShouldProcess($g.Path, "Grant $spec")) {
        $ic = @($g.Path, '/grant', $spec)
        if ($g.Recurse) { $ic += @('/T', '/C', '/Q') }
        & icacls @ic | Out-Null
    } else {
        Write-Plan ("icacls `"{0}`" /grant {1}{2}" -f $g.Path, $spec, $(if ($g.Recurse) { ' /T' } else { '' }))
    }
}

Write-Host ''
if ($WhatIfPreference) {
    Write-Host 'DRY RUN complete (-WhatIf). Nothing was changed.'
} else {
    Write-Host 'Q9 provisioning applied. Verify enforcement, then produce the attestation:'
    Write-Host '  python -m qwen_train.f2_isolation'
    Write-Host '  python -m qwen_train.f2_preflight --attestation <attestation>'
}
