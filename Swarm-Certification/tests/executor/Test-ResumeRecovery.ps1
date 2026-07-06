param([string]$JsonPath)

$repoRoot = "C:\Users\rober\Projects\v-horseshoe-v2\Swarm-Certification"
. "$repoRoot\tests\helpers\JsonAssertions.ps1"

if (-not $JsonPath) {
  $JsonPath = Join-Path $repoRoot "benchmark\outputs\executor_resume_recovery_001.json"
}

$result = Read-JsonResult -JsonPath $JsonPath
Assert-HasProperties -Object $result -Properties @("task_id","status","response","actions_taken")
Assert-StatusSuccess -Object $result

if ($result.task_id -ne "executor_resume_recovery_001") { throw "Wrong task_id" }
Assert-TextContains -Text $result.response -Terms @("resume","last successful task","preserve existing outputs")
Assert-ArrayMinCount -Value $result.actions_taken -Name "actions_taken" -MinCount 1

Write-Host "PASS: executor behavior validator passed."
exit 0
