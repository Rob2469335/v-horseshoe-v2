param()

$taskFile = Join-Path $PSScriptRoot "..\..\benchmark\tasks\Planner\planner.json"
if (-not (Test-Path $taskFile)) { throw "Missing planner task file: $taskFile" }

$taskJson = Get-Content $taskFile -Raw | ConvertFrom-Json
$task = $taskJson.tasks | Where-Object { $_.id -eq "planner_ci_cd_pipeline_001" }

if (-not $task) { throw "planner_ci_cd_pipeline_001 not found" }

$text = ($task.description + " " + $task.expected_fix).ToLowerInvariant()
$required = @("build","test","benchmark","artifact","rollback","resume","timeouts","result persistence")

$missing = $required | Where-Object { $text -notmatch [regex]::Escape($_.ToLowerInvariant()) }

if ($missing.Count -gt 0) {
  Write-Error "Planner CI/CD task missing required concepts: $($missing -join ', ')"
  exit 1
}

Write-Host "PASS: planner CI/CD task includes required pipeline concepts."
exit 0
