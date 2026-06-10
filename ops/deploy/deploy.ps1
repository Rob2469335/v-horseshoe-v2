param(
    [string]$Environment = "dev"
)

$ErrorActionPreference = "Stop"

Set-Location (Resolve-Path "$PSScriptRoot\..\..").Path

Write-Host "Phase 6 deploy starting for environment: $Environment" -ForegroundColor Cyan

Write-Host "Running smoke tests..." -ForegroundColor Yellow
& powershell -ExecutionPolicy Bypass -File ".\ops\tests\smoke_tests.ps1"

Write-Host "Deploy placeholder complete." -ForegroundColor Green
Write-Host "Next step: replace this script with real packaging, migration, and service restart logic." -ForegroundColor Green
