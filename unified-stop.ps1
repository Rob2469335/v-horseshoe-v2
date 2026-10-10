# unified-stop.ps1 - PID-scoped stop for the Swarm OS dev stack (D1).
# Dot-sources lifecycle.ps1 and stops ONLY PIDs start-dev.ps1 recorded.
# Targeting is strictly by recorded PID; it never enumerates or kills by name.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Write-Host "=== Stopping Swarm OS ===" -ForegroundColor Yellow
. (Join-Path $root "lifecycle.ps1")
$pidDir = Join-Path $root "data\pids"
Stop-RecordedServices -PidDir $pidDir
Write-Host "Done." -ForegroundColor Green
