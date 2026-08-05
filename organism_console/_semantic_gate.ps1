function Test-SemanticDiff {
    param (
        [string]$Path,
        [string]$NewContent
    )

    Write-Host "=== SEMANTIC DIFF GATE ===" -ForegroundColor Cyan

    # If file does not exist → safe
    if (!(Test-Path $Path)) {
        Write-Host "[semantic] new file → safe" -ForegroundColor Green
        return $true
    }

    # write temp file for validation
    $temp = "$env:TEMP\semantic_check.py"
    Set-Content $temp -Value $NewContent -Encoding utf8

    # try Python compile check
    $result = py -m py_compile $temp 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[BLOCKED] Python syntax error detected" -ForegroundColor Red
        Write-Host $result -ForegroundColor Yellow
        return $false
    }

    Write-Host "[OK] Valid Python syntax" -ForegroundColor Green
    return $true
}
