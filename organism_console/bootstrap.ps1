# ==============================
# H O R S E S H O E   B O O T S T R A P
# ==============================

function Start-System {
    Write-Host "=== SYSTEM BOOTING ===" -ForegroundColor Cyan

    # CORE SSRG
    . .\organism_console\_ssrg_v2.ps1

    # COMPETITION
    . .\organism_console\_competition_ssrg_bind.ps1

    # GENOME EVOLUTION
    . .\organism_console\_genome_ssrg_bind.ps1

    Write-Host "=== MODULES LOADED ===" -ForegroundColor Green
}

function Run-Task {
    param($task)

    if (-not $global:SSRG) {
        New-SSRG
    }

    # Use the new CLI client to talk to the backend
    $result = py organism_console/cli.py $task 2>&1

    Add-SSRGEvent "orchestrator" "ssrg" "TASK" $task $result 1.0

    Write-Host "[RUN] $task" -ForegroundColor Yellow
    Write-Host $result

    return $result
}

function Show-SystemState {
    Write-Host "`n=== SYSTEM STATE ===" -ForegroundColor Cyan
    if ($global:SSRG) {
        Write-Host ("Events: " + $global:SSRG.events.Count)
        Write-Host ("Log file: " + $global:SSRG.file)
    } else {
        Write-Host "SSRG not initialized"
    }
}
