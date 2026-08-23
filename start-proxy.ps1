$ErrorActionPreference = "SilentlyContinue"

Write-Host "Starting Smart Model Router on port 8080..." -ForegroundColor Cyan

$root = "C:\Users\rober\Projects\v-horseshoe-v2"
Set-Location $root

# Ensure fastapi and uvicorn are installed
.venv\Scripts\python.exe -m pip install fastapi uvicorn httpx psutil > $null

# Start the router
.venv\Scripts\python.exe -u model_router.py
