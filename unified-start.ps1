# unified-start.ps1
$ErrorActionPreference = "Stop"

Write-Host "=== Swarm OS Unified Startup ===" -ForegroundColor Cyan

Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("ollama","qdrant","uvicorn","vite","node","python")) {
    $procs = Get-Process -Name $svc -ErrorAction SilentlyContinue
    if ($procs) {
        Write-Host "Stopping $svc..." -ForegroundColor Gray
        $procs | Stop-Process -Force
        Start-Sleep -Milliseconds 300
    }
}

Write-Host "Cleanup complete ✔" -ForegroundColor Green
Start-Sleep -Seconds 1

Write-Host "`n[STEP 2] Checking Ollama..." -ForegroundColor Yellow
if (-not (Get-Process -Name ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Starting Ollama..." -ForegroundColor Gray
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
    Write-Host "Ollama started ✔" -ForegroundColor Green
} else { Write-Host "Ollama already running ✔" -ForegroundColor Green }

Write-Host "`n[STEP 3] Running start-dev.ps1..." -ForegroundColor Yellow
.\start-dev.ps1
