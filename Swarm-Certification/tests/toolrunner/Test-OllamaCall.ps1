param()

$taskFile = Join-Path $PSScriptRoot "..\..\benchmark\tasks\ToolRunner\toolrunner.json"
if (-not (Test-Path $taskFile)) { throw "Missing toolrunner task file: $taskFile" }

$taskJson = Get-Content $taskFile -Raw | ConvertFrom-Json
$task = $taskJson.tasks | Where-Object { $_.id -eq "toolrunner_ollama_call_001" }

if (-not $task) { throw "toolrunner_ollama_call_001 not found" }

$text = ($task.description + " " + $task.expected_fix).ToLowerInvariant()
$required = @("structured output","runtime metadata","timing")

$missing = $required | Where-Object { $text -notmatch [regex]::Escape($_) }

if ($missing.Count -gt 0) {
  Write-Error "ToolRunner ollama task missing required concepts: $($missing -join ', ')"
  exit 1
}

Write-Host "PASS: toolrunner ollama task requires structured output and runtime metadata."
exit 0
