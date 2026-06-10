# Organism Console - Morning Check Script
# This script performs a low-risk audit of the repository to prepare for daily development.

Write-Host "--- Starting Morning Check ---" -ForegroundColor Cyan

# 1. Git Status
Write-Host "`n[1/5] Checking Git Status..." -ForegroundColor Yellow
git status

# 2. Clutter Check
Write-Host "`n[2/5] Checking for leftover backups and temporary files..." -ForegroundColor Yellow
$clutter = Get-ChildItem -Path src -Include *.bak,*.bak.*,*.bad,*.current.txt,*.interactive.bak,*.live-working.bak -Recurse -ErrorAction SilentlyContinue
if ($clutter) {
    Write-Host "Found $($clutter.Count) clutter files in src/. Recommend deletion after browser smoke test." -ForegroundColor Magenta
    $clutter | Select-Object -ExpandProperty FullName | ForEach-Object { Write-Host " - $_" }
} else {
    Write-Host "No clutter files found in src/." -ForegroundColor Green
}

# 3. Frontend Validation
Write-Host "`n[3/5] Running Frontend Validation (Type-check & Build)..." -ForegroundColor Yellow
npm run build
if ($LASTEXITCODE -eq 0) {
    Write-Host "Frontend build passed." -ForegroundColor Green
} else {
    Write-Host "Frontend build FAILED." -ForegroundColor Red
}

# 4. Backend Validation
Write-Host "`n[4/5] Running Backend Syntax Validation..." -ForegroundColor Yellow
python -m py_compile main.py
if ($LASTEXITCODE -eq 0) {
    Write-Host "Backend syntax check passed." -ForegroundColor Green
} else {
    Write-Host "Backend syntax check FAILED." -ForegroundColor Red
}

# 5. Risk Audit (Grep-style)
Write-Host "`n[5/5] Auditing for known risky leftovers..." -ForegroundColor Yellow
$riskyPatterns = @(
    "maxWidth: 1440",
    "margin: `"0 auto`"",
    "getTraces\(",
    "under pressure"
)

$riskFound = $false
foreach ($pattern in $riskyPatterns) {
    $matches = Select-String -Path "src/**/*.tsx", "src/**/*.css" -Pattern $pattern -Exclude "*.bak","*.bad","*.txt" -ErrorAction SilentlyContinue
    if ($matches) {
        Write-Host "Potential leftover found for pattern: '$pattern'" -ForegroundColor Magenta
        $matches | ForEach-Object { Write-Host " - $($_.Path):$($_.LineNumber)" }
        $riskFound = $true
    }
}

if (-not $riskFound) {
    Write-Host "No risky leftovers found in active source files." -ForegroundColor Green
}

Write-Host "`n--- Morning Check Complete ---" -ForegroundColor Cyan
Write-Host "Ready for operations." -ForegroundColor Green
