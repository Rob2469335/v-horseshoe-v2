function Show-DiffGate {
    param (
        [string]$Path,
        [string]$NewContent
    )

    if (!(Test-Path $Path)) {
        Write-Host "[diff] new file → safe" -ForegroundColor Green
        return $true
    }

    $old = Get-Content $Path -Raw
    $new = $NewContent

    Write-Host "=== DIFF GATE ===" -ForegroundColor Cyan

    if ($old -eq $new) {
        Write-Host "[diff] no changes detected" -ForegroundColor Yellow
        return $false
    }

    $oldLines = $old -split "`n"
    $newLines = $new -split "`n"

    Write-Host "`n--- OLD vs NEW ---`n"

    Compare-Object $oldLines $newLines | ForEach-Object {
        if ($_.SideIndicator -eq "<=") {
            Write-Host "- $($_.InputObject)" -ForegroundColor Red
        }
        elseif ($_.SideIndicator -eq "=>") {
            Write-Host "+ $($_.InputObject)" -ForegroundColor Green
        }
    }

    Write-Host "`n=== RISK CHECK ==="

    $risk = 0

    # SAFE PRECOMPUTED FLAGS (NO METHOD CHAINS INSIDE CONDITIONS)
    $hasNewlineEscape = $new.Contains("`n")
    $hasReplaceWord   = $new.Contains("replace")
    $hasSetContent    = $new.Contains("Set-Content")

    $doubleSelf       = ($new.Contains("self.") -and $new.Contains("self."))
    $bigChange        = ($new.Length -gt ($old.Length * 2))

    if ($hasNewlineEscape) { $risk = $risk + 3 }
    if ($hasReplaceWord -and $hasSetContent) { $risk = $risk + 5 }
    if ($doubleSelf) { $risk = $risk + 2 }
    if ($bigChange) { $risk = $risk + 2 }

    if ($risk -ge 5) {
        Write-Host "[BLOCKED] High-risk change detected (score: $risk)" -ForegroundColor Red
        return $false
    }

    Write-Host "[OK] Safe change (score: $risk)" -ForegroundColor Green
    return $true
}
