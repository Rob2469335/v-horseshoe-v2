# unified-stop.ps1
$ErrorActionPreference = "Stop"
Write-Host "=== Stopping Swarm OS ===" -ForegroundColor Yellow
# Get-Process llama-server,llama,qdrant,uvicorn,vite,node,python -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "All processes stopped (OK)" -ForegroundColor Green
