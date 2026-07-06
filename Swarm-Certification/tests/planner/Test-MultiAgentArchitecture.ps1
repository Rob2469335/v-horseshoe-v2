param([string]$JsonPath)

$repoRoot = "C:\Users\rober\Projects\v-horseshoe-v2\Swarm-Certification"
. "$repoRoot\tests\helpers\JsonAssertions.ps1"

if (-not $JsonPath) {
  $JsonPath = Join-Path $repoRoot "benchmark\outputs\planner_multi_agent_architecture_001.json"
}

$result = Read-JsonResult -JsonPath $JsonPath
Assert-HasProperties -Object $result -Properties @("task_id","status","response","plan")
Assert-StatusSuccess -Object $result

if ($result.task_id -ne "planner_multi_agent_architecture_001") { throw "Wrong task_id" }
Assert-TextContains -Text $result.response -Terms @("coordinator","planner","researcher","executor","coder","reviewer","debugger","tool runner")
Assert-ArrayMinCount -Value $result.plan -Name "plan" -MinCount 1

Write-Host "PASS: planner behavior validator passed."
exit 0
