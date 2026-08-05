param(
    [string]$ProjectRoot = "C:\Users\rober\Projects\v-horseshoe-v2",
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [switch]$Json
)

$ErrorActionPreference = "SilentlyContinue"

function Add-Result {
    param(
        [string]$Area,
        [string]$Check,
        [int]$Score,
        [int]$MaxScore,
        [string]$Evidence,
        [string]$Missing
    )

    [pscustomobject]@{
        Area = $Area
        Check = $Check
        Score = $Score
        MaxScore = $MaxScore
        Evidence = $Evidence
        Missing = $Missing
    }
}

function Find-Pattern {
    param(
        [string[]]$Paths,
        [string[]]$Patterns
    )

    $matches = @()
    foreach ($path in $Paths) {
        if (Test-Path $path) {
            foreach ($pattern in $Patterns) {
                $found = Select-String -Path $path -Pattern $pattern -SimpleMatch
                if ($found) {
                    $matches += $found
                }
            }
        }
    }
    $matches
}

$results = @()

$backendStatus = $null
$backendReady = $false
try {
    $backendStatus = Invoke-RestMethod "$BackendUrl/status"
    $backendReady = $true
} catch {}

$backendFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os") -Recurse -File -Include *.py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$frontendFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console\src") -Recurse -File -Include *.ts,*.tsx -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$allFiles = $backendFiles + $frontendFiles

$detectMatches = Find-Pattern -Paths $allFiles -Patterns @("anomaly", "health", "status", "readyz", "recover")
$detectScore = if ($detectMatches.Count -gt 10 -and $backendReady) { 2 } elseif ($detectMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Detection" "Automatic detection signals" $detectScore 2 ("{0} code matches; backend /status {1}" -f $detectMatches.Count, $(if($backendReady){"reachable"}else{"unreachable"})) "Need broader automatic anomaly detection and verified detection drills"

$diagMatches = Find-Pattern -Paths $allFiles -Patterns @("error", "cause", "reason", "classif", "trace")
$diagScore = if ($diagMatches.Count -gt 10) { 2 } elseif ($diagMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Diagnosis" "Root-cause and trace evidence" $diagScore 2 ("{0} code matches for error/cause/trace" -f $diagMatches.Count) "Need stronger root-cause classification and incident explanations"

$remMatches = Find-Pattern -Paths $allFiles -Patterns @("repair", "remed", "retry", "fallback", "recover", "heal")
$remScore = if ($remMatches.Count -gt 10) { 2 } elseif ($remMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Remediation" "Automatic corrective actions" $remScore 2 ("{0} remediation-related code matches" -f $remMatches.Count) "Need bounded automatic remediation paths tied to known failure modes"

$valMatches = Find-Pattern -Paths $allFiles -Patterns @("verify", "validated", "assert", "health", "ready")
$valScore = if ($valMatches.Count -gt 15) { 2 } elseif ($valMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Validation" "Post-action verification" $valScore 2 ("{0} verification-related matches" -f $valMatches.Count) "Need explicit post-remediation success checks and recovery confirmation"

$learnMatches = Find-Pattern -Paths $allFiles -Patterns @("learn", "memory", "feedback", "adapt", "history")
$learnScore = if ($learnMatches.Count -gt 10) { 2 } elseif ($learnMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Learning" "Outcome-based adaptation" $learnScore 2 ("{0} learning-related matches" -f $learnMatches.Count) "Need feedback loops that improve future decisions from prior outcomes"

$guardMatches = Find-Pattern -Paths $allFiles -Patterns @("manual", "override", "guard", "limit", "policy")
$guardScore = if ($guardMatches.Count -gt 10) { 2 } elseif ($guardMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Guardrails" "Human override and safety limits" $guardScore 2 ("{0} guardrail-related matches" -f $guardMatches.Count) "Need explicit policy boundaries and stronger safe-operating-envelope enforcement"

$obsMatches = Find-Pattern -Paths $allFiles -Patterns @("trace", "metrics", "events", "status", "dashboard")
$obsScore = if ($obsMatches.Count -gt 15 -and $backendReady) { 2 } elseif ($obsMatches.Count -gt 0) { 1 } else { 0 }
$results += Add-Result "Observability" "Traces, metrics, and runtime state" $obsScore 2 ("{0} observability-related matches" -f $obsMatches.Count) "Need fuller runtime evidence linking decisions, actions, and outcomes"

$toolCount = 0
try {
    if ($backendStatus.tools) { $toolCount = @($backendStatus.tools).Count }
    elseif ($backendStatus.capabilities.tools.count) { $toolCount = [int]$backendStatus.capabilities.tools.count }
} catch {}
$toolScore = if ($toolCount -ge 3) { 2 } elseif ($toolCount -ge 1) { 1 } else { 0 }
$results += Add-Result "Tool Reach" "Executable action surface" $toolScore 2 ("Detected tool count: {0}" -f $toolCount) "Need enough callable tools to perform meaningful autonomous actions"

$pytestIni = Join-Path $ProjectRoot "pytest.ini"
$unitTests = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os\tests\unit") -Recurse -File -ErrorAction SilentlyContinue).Count
$intTests = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os\tests\integration") -Recurse -File -ErrorAction SilentlyContinue).Count
$vitestFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console\src") -Recurse -File -Include *.test.ts,*.test.tsx -ErrorAction SilentlyContinue).Count
$relScore = if ((Test-Path $pytestIni) -and $unitTests -gt 0 -and $intTests -gt 0 -and $vitestFiles -gt 0) { 2 } elseif ($unitTests -gt 0 -or $intTests -gt 0 -or $vitestFiles -gt 0) { 1 } else { 0 }
$results += Add-Result "Reliability" "Automated test coverage presence" $relScore 2 ("unit files={0}; integration files={1}; frontend test files={2}" -f $unitTests, $intTests, $vitestFiles) "Need repeated fault-injection and resilience drills, not only conventional tests"

$playwrightConfig = Join-Path $ProjectRoot "organism-console\playwright.config.ts"
$e2eFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console") -Recurse -File -Include *.spec.ts,*.e2e.ts -ErrorAction SilentlyContinue).Count
$e2eScore = if ((Test-Path $playwrightConfig) -and $e2eFiles -gt 0) { 2 } elseif ($e2eFiles -gt 0) { 1 } else { 0 }
$results += Add-Result "E2E Autonomy" "Browser and closed-loop autonomy drills" $e2eScore 2 ("playwright.config.ts={0}; e2e files={1}" -f (Test-Path $playwrightConfig), $e2eFiles) "Need real end-to-end autonomy scenarios and recovery drills"

$total = ($results | Measure-Object -Property Score -Sum).Sum
$max = ($results | Measure-Object -Property MaxScore -Sum).Sum

$summary = [pscustomobject]@{
    ProjectRoot = $ProjectRoot
    BackendUrl = $BackendUrl
    TotalScore = $total
    MaxScore = $max
    Percent = [math]::Round(($total / $max) * 100, 1)
    Rating = if ($total -le 5) { "Instrumented app, not autonomous" } elseif ($total -le 10) { "Assisted autonomy / early self-healing foundation" } elseif ($total -le 15) { "Bounded autonomy" } else { "Mature autonomous/self-healing operation" }
    BackendReachable = $backendReady
    Timestamp = (Get-Date).ToString("s")
}

if ($Json) {
    [pscustomobject]@{
        Summary = $summary
        Results = $results
    } | ConvertTo-Json -Depth 6
    exit 0
}

Write-Host "=== AUTONOMY AUDIT ===" -ForegroundColor Cyan
$summary | Format-List
Write-Host ""
$results | Format-Table Area, Score, MaxScore, Check, Evidence, Missing -AutoSize
