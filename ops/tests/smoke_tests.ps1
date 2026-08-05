$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..").Path

Write-Host "Phase 6 smoke tests" -ForegroundColor Cyan

python -c "from infrastructure.config.settings import get_settings; get_settings(); print('settings ok')"
python -c "from infrastructure.runtime.feature_flags import get_feature_flags; get_feature_flags(); print('flags ok')"
python -c "from infrastructure.vector.qdrant_collections import get_collection_specs; print(len(get_collection_specs()))"
python -c "from infrastructure.cache.cache_provider import get_cache_provider; print(type(get_cache_provider()).__name__)"
python -c "from ops.health.system_health import run_system_health_checks; print(len(run_system_health_checks()))"

try {
  $health = Invoke-RestMethod "http://127.0.0.1:8000/health"
  Write-Host "[OK] /health => $($health.status)" -ForegroundColor Green
}
catch {
  Write-Host "[WARN] /health not reachable; import checks still passed" -ForegroundColor Yellow
}

try {
  $sys = Invoke-RestMethod "http://127.0.0.1:8000/health/system"
  Write-Host "[OK] /health/system reachable" -ForegroundColor Green
}
catch {
  Write-Host "[WARN] /health/system not reachable yet" -ForegroundColor Yellow
}

Write-Host "Smoke tests completed." -ForegroundColor Green
