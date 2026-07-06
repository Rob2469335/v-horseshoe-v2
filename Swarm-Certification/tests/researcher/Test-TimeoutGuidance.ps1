param()

$taskFile = Join-Path $PSScriptRoot "..\..\benchmark\tasks\Researcher\researcher.json"
if (-not (Test-Path $taskFile)) { throw "Missing researcher task file: $taskFile" }

$taskJson = Get-Content $taskFile -Raw | ConvertFrom-Json
$task = $taskJson.tasks | Where-Object { $_.id -eq "researcher_model_timeout_001" }

if (-not $task) { throw "researcher_model_timeout_001 not found" }

$text = ($task.description + " " + $task.expected_fix).ToLowerInvariant()
$required = @("timeout","retry","guidance","source")

$missing = $required | Where-Object { $text -notmatch [regex]::Escape($_) }

if ($missing.Count -gt 0) {
  Write-Error "Researcher timeout task missing required concepts: $($missing -join ', ')"
  exit 1
}

Write-Host "PASS: researcher timeout task includes timeout and retry expectations."
exit 0
