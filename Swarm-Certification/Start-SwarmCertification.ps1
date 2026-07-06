param(
    [int]$MaxOpenRouterCalls = 25,
    [int]$ModelTimeoutSec = 240,
    [switch]$Force
)

Set-Location $PSScriptRoot
. "$PSScriptRoot/engine/Metrics.ps1"
. "$PSScriptRoot/engine/Runner.ps1"

Write-Host "=== Starting Swarm Certification ==="
Invoke-BenchmarkRun -MaxOpenRouterCalls $MaxOpenRouterCalls -ModelTimeoutSec $ModelTimeoutSec -Force:$Force
Write-Host "=== Run complete. Outputs saved to benchmark/outputs ==="
