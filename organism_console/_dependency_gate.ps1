function Test-DependencyGate {
    param (
        [string]$Path,
        [string]$Content
    )

    Write-Host "=== DEPENDENCY GATE v2 ===" -ForegroundColor Cyan

    # STEP 1: syntax check via temp compile
    $temp = "$env:TEMP\dep_check.py"
    Set-Content $temp -Value $Content -Encoding utf8

    $compile = py -m py_compile $temp 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[BLOCKED] Syntax error" -ForegroundColor Red
        Write-Host $compile
        return $false
    }

    # STEP 2: extract imports (simple static scan)
    $imports = @()
    foreach ($line in $Content -split "`n") {
        if ($line -match "^from\s+([a-zA-Z0-9_\.]+)\s+import") {
            $imports += $matches[1]
        }
        elseif ($line -match "^import\s+([a-zA-Z0-9_\.]+)") {
            $imports += $matches[1]
        }
    }

    # STEP 3: validate imports exist
    foreach ($mod in $imports) {
        $check = py -c "import importlib.util; print(importlib.util.find_spec('$mod') is not None)" 2>$null

        if ($check -notmatch "True") {
            Write-Host "[WARNING] Missing dependency: $mod" -ForegroundColor Yellow
        }
    }

    # STEP 4: structural risk check (critical system modules)
    $critical = @(
        "organism_console.core.orchestrator",
        "organism_console.core.competition_layer",
        "organism_console.review.reviewer",
        "organism_console.skills.skill_memory_engine"
    )

    foreach ($c in $critical) {
        if ($Content.Contains($c)) {
            Write-Host "[INFO] touches core module: $c" -ForegroundColor DarkYellow
        }
    }

    Write-Host "[OK] Dependency gate passed" -ForegroundColor Green
    return $true
}
