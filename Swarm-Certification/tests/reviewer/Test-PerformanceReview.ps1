param()

$taskFile = Join-Path $PSScriptRoot "..\..\benchmark\tasks\Reviewer\reviewer.json"
if (-not (Test-Path $taskFile)) { throw "Missing reviewer task file: $taskFile" }

$taskJson = Get-Content $taskFile -Raw | ConvertFrom-Json
$task = $taskJson.tasks | Where-Object { $_.id -eq "reviewer_performance_regression_001" }

if (-not $task) { throw "reviewer_performance_regression_001 not found" }

$text = ($task.description + " " + $task.expected_fix).ToLowerInvariant()

if ($text -notmatch "performance") {
  Write-Error "Reviewer performance task does not mention performance."
  exit 1
}

if ($text -notmatch "regression|slows down") {
  Write-Error "Reviewer performance task does not clearly target regression behavior."
  exit 1
}

Write-Host "PASS: reviewer performance task checks regression review expectations."
exit 0
