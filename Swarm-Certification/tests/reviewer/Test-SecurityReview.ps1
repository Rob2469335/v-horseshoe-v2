param([string]$JsonPath)

$repoRoot = "C:\Users\rober\Projects\v-horseshoe-v2\Swarm-Certification"
. "$repoRoot\tests\helpers\JsonAssertions.ps1"

if (-not $JsonPath) {
  $JsonPath = Join-Path $repoRoot "benchmark\outputs\reviewer_flawed_patch_security_001.json"
}

$result = Read-JsonResult -JsonPath $JsonPath
Assert-HasProperties -Object $result -Properties @("task_id","status","response","findings")
Assert-StatusSuccess -Object $result

if ($result.task_id -ne "reviewer_flawed_patch_security_001") { throw "Wrong task_id" }
Assert-TextContains -Text $result.response -Terms @("security","vulnerability","safer")
Assert-ArrayMinCount -Value $result.findings -Name "findings" -MinCount 1

Write-Host "PASS: reviewer behavior validator passed."
exit 0
