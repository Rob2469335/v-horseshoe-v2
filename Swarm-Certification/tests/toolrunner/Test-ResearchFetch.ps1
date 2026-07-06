param([string]$JsonPath)

$repoRoot = "C:\Users\rober\Projects\v-horseshoe-v2\Swarm-Certification"
. "$repoRoot\tests\helpers\JsonAssertions.ps1"

if (-not $JsonPath) {
  $JsonPath = Join-Path $repoRoot "benchmark\outputs\toolrunner_research_fetch_001.json"
}

$result = Read-JsonResult -JsonPath $JsonPath
Assert-HasProperties -Object $result -Properties @("task_id","status","response","sources","citations")
Assert-StatusSuccess -Object $result

if ($result.task_id -ne "toolrunner_research_fetch_001") { throw "Wrong task_id" }
Assert-TextContains -Text $result.response -Terms @("source","provenance","citation")
Assert-ArrayMinCount -Value $result.sources -Name "sources" -MinCount 1
Assert-ArrayMinCount -Value $result.citations -Name "citations" -MinCount 1

Write-Host "PASS: toolrunner behavior validator passed."
exit 0
