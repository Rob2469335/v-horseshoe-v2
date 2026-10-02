$ErrorActionPreference = "SilentlyContinue"

Write-Host "Starting Smart Model Router on port 8080..." -ForegroundColor Cyan

$root = "C:\Users\rober\Projects\v-horseshoe-v2"
Set-Location $root

# Dependency CHECK, not install. Installing at startup mutated the environment on
# every launch (D4: no environment mutation during startup). Dependencies belong to
# environment setup; startup only verifies they are importable and fails clearly.
.venv\Scripts\python.exe -c "import fastapi, uvicorn, httpx, psutil"
if ($LASTEXITCODE -ne 0) {
  Write-Host "Proxy dependencies are missing. Install them once with:" -ForegroundColor Red
  Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Red
  exit 1
}

# Start the router
.venv\Scripts\python.exe -u model_router.py
