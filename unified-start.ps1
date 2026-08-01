# unified-start.ps1
$ErrorActionPreference = "Stop"

Write-Host "=== Swarm OS Unified Startup ===" -ForegroundColor Cyan

Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("llama-server","llama","qdrant","uvicorn","vite","node","python")) {
    $procs = Get-Process -Name $svc -ErrorAction SilentlyContinue
    if ($procs) {
        Write-Host "Stopping $svc..." -ForegroundColor Gray
        $procs | Stop-Process -Force
        Start-Sleep -Milliseconds 300
    }
}

Write-Host "Cleanup complete (OK)" -ForegroundColor Green
Start-Sleep -Seconds 1

Write-Host "`n[STEP 2] Checking LLM Server..." -ForegroundColor Yellow
if (-not (Get-Process -Name llama-server -ErrorAction SilentlyContinue)) {
    Write-Host "No llama-server detected (will rely on cloud models or local server if launched separately) (OK)" -ForegroundColor Green
} else { Write-Host "llama-server already running (OK)" -ForegroundColor Green }

Write-Host "`n[STEP 3] Running start-dev.ps1..." -ForegroundColor Yellow
.\start-dev.ps1
