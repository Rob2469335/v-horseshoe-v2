# unified-stop.ps1
$ErrorActionPreference = "Stop"
Write-Host "=== Stopping Swarm OS ===" -ForegroundColor Yellow
Get-Process ollama,qdrant,uvicorn,vite,node,python -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "All processes stopped ✔" -ForegroundColor Green
