<#
.SYNOPSIS
    self_heal.ps1 — Detect → Diagnose → Remediate → Record → Verify

.DESCRIPTION
    Runs the full self-healing loop for v-horseshoe-v2 test hygiene.

    Modes
    ------
    -Mode Detect   : scan only, write report, exit
    -Mode Propose  : scan + show proposed remediations (dry-run)
    -Mode Approve  : scan + execute safe remediations (archive/move/patch)
    -Mode Auto     : scan + execute ALL remediations including deletions

    After any remediation mode, runs pytest to verify the suite stays green
    and records the outcome to logs/healing_history.json.

.EXAMPLE
    # Default — detect + propose only
    .\scripts\self_heal.ps1

    # Execute safe remediations, then verify
    .\scripts\self_heal.ps1 -Mode Approve

    # Full autonomous loop
    .\scripts\self_heal.ps1 -Mode Auto
#>
param(
    [ValidateSet("Detect","Propose","Approve","Auto")]
    [string]$Mode = "Propose"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot   = $PSScriptRoot | Split-Path -Parent
$ScriptsDir = Join-Path $RepoRoot "scripts"
$LogsDir    = Join-Path $RepoRoot "logs"

function Write-Step([string]$Msg) {
    Write-Host "`n━━━ $Msg ━━━" -ForegroundColor Cyan
}

function Assert-Python {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Error "python not found in PATH"
        exit 1
    }
}

Assert-Python

# ── PHASE 1: DETECT ────────────────────────────────────────────────────────
Write-Step "PHASE 1 — Detect & Diagnose"
python "$ScriptsDir\detect_stale_tests.py" --repo-root $RepoRoot
$detectExit = $LASTEXITCODE

Write-Host "`nDetect exit code: $detectExit"
if ($detectExit -eq 0) {
    Write-Host "  ✓ No errors detected." -ForegroundColor Green
} else {
    Write-Host "  ⚠ Errors detected — see logs/stale_test_report.json" -ForegroundColor Yellow
}

if ($Mode -eq "Detect") {
    Write-Host "`n[self_heal] Mode=Detect. Stopping after detection." -ForegroundColor Gray
    exit $detectExit
}

# ── PHASE 2: REMEDIATE ─────────────────────────────────────────────────────
Write-Step "PHASE 2 — Remediate"

$remediateFlag = switch ($Mode) {
    "Propose" { "--dry-run" }
    "Approve" { "--approve" }
    "Auto"    { "--auto"    }
}

python "$ScriptsDir\remediate_stale_tests.py" `
    --report "$LogsDir\stale_test_report.json" `
    $remediateFlag

$remediateExit = $LASTEXITCODE

# ── PHASE 3: VERIFY (only if something was actually remediated) ─────────────
if ($Mode -ne "Propose") {
    Write-Step "PHASE 3 — Verify (pytest)"
    Push-Location $RepoRoot
    try {
        pytest --tb=short -q
        $pytestExit = $LASTEXITCODE
        if ($pytestExit -eq 0) {
            Write-Host "`n  ✓ pytest GREEN" -ForegroundColor Green
        } else {
            Write-Host "`n  ✗ pytest FAILED — check output above" -ForegroundColor Red
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "`n[self_heal] Mode=Propose — skipping pytest run." -ForegroundColor Gray
    $pytestExit = 0
}

# ── PHASE 4: RECORD ────────────────────────────────────────────────────────
Write-Step "PHASE 4 — Record & Learn"
python "$ScriptsDir\record_healing_event.py" --refresh

# Final summary
Write-Host ""
Write-Host "━━━ Self-Heal Summary ━━━" -ForegroundColor Cyan
Write-Host "  Mode        : $Mode"
Write-Host "  Detect exit : $detectExit"
Write-Host "  Pytest exit : $pytestExit"
Write-Host "  Report      : $LogsDir\stale_test_report.json"
Write-Host "  History     : $LogsDir\healing_history.json"
Write-Host "  Policies    : $LogsDir\policy_hints.json"

if ($pytestExit -ne 0) { exit $pytestExit }
exit 0
