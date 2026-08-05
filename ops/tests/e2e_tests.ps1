$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..").Path

Write-Host "Phase 6 e2e checks" -ForegroundColor Cyan

$base = "http://127.0.0.1:8000"

$health = Invoke-RestMethod "$base/health"
$status = Invoke-RestMethod "$base/status"
$tools  = Invoke-RestMethod "$base/tools"
$time   = Invoke-RestMethod "$base/timeline?window_minutes=60"

Write-Host "[OK] /health status: $($health.status)" -ForegroundColor Green
Write-Host "[OK] /status ready: $($status.ready)" -ForegroundColor Green
Write-Host "[OK] /tools count: $($tools.count)" -ForegroundColor Green
Write-Host "[OK] /timeline window: $($time.window_minutes)" -ForegroundColor Green

try {
  $system = Invoke-RestMethod "$base/health/system"
  Write-Host "[OK] /health/system checks: $($system.checks.Count)" -ForegroundColor Green
}
catch {
  throw "/health/system failed"
}

$qdrant = Invoke-RestMethod "http://127.0.0.1:6333/collections"
Write-Host "[OK] Qdrant reachable" -ForegroundColor Green
$qdrant | ConvertTo-Json -Depth 8
