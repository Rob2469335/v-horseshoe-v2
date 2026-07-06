param()

$taskFile = Join-Path $PSScriptRoot "..\..\benchmark\tasks\Executor\executor.json"
if (-not (Test-Path $taskFile)) { throw "Missing executor task file: $taskFile" }

$taskJson = Get-Content $taskFile -Raw | ConvertFrom-Json
$task = $taskJson.tasks | Where-Object { $_.id -eq "executor_file_repair_001" }

if (-not $task) { throw "executor_file_repair_001 not found" }

$text = ($task.description + " " + $task.expected_fix).ToLowerInvariant()

if ($text -notmatch "patch" -or $text -notmatch "file edits") {
  Write-Error "Executor file repair task does not clearly require patching and exact edits."
  exit 1
}

if ($text -notmatch "without changing unrelated behavior") {
  Write-Error "Executor file repair task does not constrain unrelated changes."
  exit 1
}

Write-Host "PASS: executor file repair task enforces targeted modification."
exit 0
