param(
    [string]$ProjectRoot = "C:\Users\rober\Projects\v-horseshoe-v2",
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [switch]$Json
)

$ErrorActionPreference = "SilentlyContinue"

function New-ScoreRow {
    param(
        [string]$Section,
        [string]$Area,
        [int]$Score,
        [int]$MaxScore,
        [string]$Evidence,
        [string]$Missing
    )

    [pscustomobject]@{
        Section  = $Section
        Area     = $Area
        Score    = $Score
        MaxScore = $MaxScore
        Evidence = $Evidence
        Missing  = $Missing
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
                if ($found) { $matches += $found }
            }
        }
    }
    $matches
}

function Get-JsonSafely {
    param([string]$Url)
    try { Invoke-RestMethod $Url } catch { $null }
}

$backendFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os") -Recurse -File -Include *.py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$frontendFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console\src") -Recurse -File -Include *.ts,*.tsx -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
$allFiles = $backendFiles + $frontendFiles

$results = @()

# -------------------------
# STATIC READINESS
# -------------------------

$detectMatches = Find-Pattern -Paths $allFiles -Patterns @("anomaly", "health", "status", "readyz", "recover")
$detectScore = if ($detectMatches.Count -gt 10) { 2 } elseif ($detectMatches.Count -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Detection scaffolding" $detectScore 2 ("keyword hits={0}" -f $detectMatches.Count) "Need clearer detection pathways in code and tests"

$diagMatches = Find-Pattern -Paths $allFiles -Patterns @("trace", "error", "reason", "cause", "diagn")
$diagScore = if ($diagMatches.Count -gt 10) { 2 } elseif ($diagMatches.Count -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Diagnosis scaffolding" $diagScore 2 ("keyword hits={0}" -f $diagMatches.Count) "Need stronger root-cause encoding and explanation paths"

$remMatches = Find-Pattern -Paths $allFiles -Patterns @("retry", "fallback", "recover", "heal", "remed")
$remScore = if ($remMatches.Count -gt 10) { 2 } elseif ($remMatches.Count -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Remediation scaffolding" $remScore 2 ("keyword hits={0}" -f $remMatches.Count) "Need explicit auto-remediation flows"

$guardMatches = Find-Pattern -Paths $allFiles -Patterns @("manual", "override", "policy", "guard", "limit")
$guardScore = if ($guardMatches.Count -gt 10) { 2 } elseif ($guardMatches.Count -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Guardrails in code" $guardScore 2 ("keyword hits={0}" -f $guardMatches.Count) "Need clearer constraints and human override controls"

$obsMatches = Find-Pattern -Paths $allFiles -Patterns @("trace", "metrics", "events", "status", "dashboard")
$obsScore = if ($obsMatches.Count -gt 15) { 2 } elseif ($obsMatches.Count -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Observability surfaces" $obsScore 2 ("keyword hits={0}" -f $obsMatches.Count) "Need tighter linkage between events, decisions, and outcomes"

$pytestIni = Test-Path (Join-Path $ProjectRoot "pytest.ini")
$unitTestFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os\tests\unit") -Recurse -File -ErrorAction SilentlyContinue).Count
$intTestFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "swarm_os\tests\integration") -Recurse -File -ErrorAction SilentlyContinue).Count
$frontendTestFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console\src") -Recurse -File -Include *.test.ts,*.test.tsx -ErrorAction SilentlyContinue).Count
$testScore = if ($pytestIni -and $unitTestFiles -gt 0 -and $intTestFiles -gt 0 -and $frontendTestFiles -gt 0) { 2 } elseif ($unitTestFiles -gt 0 -or $intTestFiles -gt 0 -or $frontendTestFiles -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "Automated test base" $testScore 2 ("unit={0}; integration={1}; frontend={2}" -f $unitTestFiles, $intTestFiles, $frontendTestFiles) "Need broader fault-injection and autonomous scenario tests"

$playwrightConfig = Test-Path (Join-Path $ProjectRoot "organism-console\playwright.config.ts")
$e2eFiles = @(Get-ChildItem -Path (Join-Path $ProjectRoot "organism-console") -Recurse -File -Include *.spec.ts,*.e2e.ts -ErrorAction SilentlyContinue).Count
$e2eStaticScore = if ($playwrightConfig -and $e2eFiles -gt 0) { 2 } elseif ($e2eFiles -gt 0) { 1 } else { 0 }
$results += New-ScoreRow "Static Readiness" "E2E test scaffolding" $e2eStaticScore 2 ("playwright.config.ts={0}; e2e files={1}" -f $playwrightConfig, $e2eFiles) "Need browser-level autonomy and recovery scenarios"

# -------------------------
# RUNTIME AUTONOMY PROOF
# -------------------------

$status = Get-JsonSafely "$BackendUrl/status"
$health = Get-JsonSafely "$BackendUrl/health"
$readyz = Get-JsonSafely "$BackendUrl/readyz"
$tools = Get-JsonSafely "$BackendUrl/tools"
$traceSummary = Get-JsonSafely "$BackendUrl/trace-summary"

$backendReachable = $null -ne $status
$reachScore = if ($backendReachable -and $null -ne $health -and $null -ne $readyz) { 2 } elseif ($backendReachable) { 1 } else { 0 }
$results += New-ScoreRow "Runtime Proof" "Live backend reachability" $reachScore 2 ("status={0}; health={1}; readyz={2}" -f ($null -ne $status), ($null -ne $health), ($null -ne $readyz)) "Need stable live endpoints for operational proof"

$toolCount = 0
try {
    if ($tools.tools) { $toolCount = @($tools.tools).Count }
    elseif ($status.tools) { $toolCount = @($status.tools).Count }
    elseif ($status.capabilities.tools.count) { $toolCount = [int]$status.capabilities.tools.count }
} catch {}
$toolRuntimeScore = if ($toolCount -ge 3) { 2 } elseif ($toolCount -ge 1) { 1 } else { 0 }
$results += New-ScoreRow "Runtime Proof" "Live tool reach" $toolRuntimeScore 2 ("toolCount={0}" -f $toolCount) "Need enough live tools to perform meaningful autonomous action"

$visionAvailable = $false
$ollamaReachable = $false
$manualOnly = $null
try {
    $visionAvailable = [bool]($status.capabilities.vision.available)
} catch {}
try {
    $ollamaReachable = [bool]($status.ollama_reachable)
} catch {}
try {
    $manualOnly = $status.capabilities.manual_only
} catch {}
$capScore = 0
if ($visionAvailable -or $ollamaReachable) { $capScore++ }
if ($manualOnly -eq $false) { $capScore++ }
$results += New-ScoreRow "Runtime Proof" "Live autonomy capability exposure" $capScore 2 ("visionAvailable={0}; ollamaReachable={1}; manualOnly={2}" -f $visionAvailable, $ollamaReachable, $manualOnly) "Need live capability exposure beyond manual-only operation"

$traceCount = 0
try {
    if ($traceSummary -is [System.Array]) { $traceCount = @($traceSummary).Count }
    elseif ($traceSummary.traces) { $traceCount = @($traceSummary.traces).Count }
    elseif ($traceSummary.items) { $traceCount = @($traceSummary.items).Count }
} catch {}
$traceScore = if ($traceCount -ge 1) { 2 } elseif ($backendReachable) { 1 } else { 0 }
$results += New-ScoreRow "Runtime Proof" "Trace evidence of live behavior" $traceScore 2 ("traceCount={0}" -f $traceCount) "Need live traces proving action sequences and outcomes"

$recoverySignals = 0
try {
    $statusJson = $status | ConvertTo-Json -Depth 8
    foreach ($pattern in @("recover", "degraded", "fallback", "retry", "repair")) {
        if ($statusJson -match $pattern) { $recoverySignals++ }
    }
} catch {}
$recoveryScore = if ($recoverySignals -ge 2) { 2 } elseif ($recoverySignals -ge 1) { 1 } else { 0 }
$results += New-ScoreRow "Runtime Proof" "Runtime recovery/degradation signals" $recoveryScore 2 ("status signal hits={0}" -f $recoverySignals) "Need visible live recovery state and remediation outcomes"

$validationScore = 0
if ($null -ne $health) { $validationScore++ }
if ($null -ne $readyz) { $validationScore++ }
$results += New-ScoreRow "Runtime Proof" "Post-action validation endpoints" $validationScore 2 ("health={0}; readyz={1}" -f ($null -ne $health), ($null -ne $readyz)) "Need stronger post-action recovery confirmation logic"

$learningScore = 0
$learningEvidence = "No runtime learning proof found"
$results += New-ScoreRow "Runtime Proof" "Observed runtime learning" $learningScore 2 $learningEvidence "Need repeated scenario evidence showing better future handling from past outcomes"

$e2eProofScore = 0
$e2eProofEvidence = if ($e2eFiles -gt 0) { "E2E files exist but no runtime drill proof captured" } else { "No E2E files detected" }
$results += New-ScoreRow "Runtime Proof" "Closed-loop autonomy drills" $e2eProofScore 2 $e2eProofEvidence "Need fault injection, autonomous action, and recovery verification in one run"

$staticTotal = ($results | Where-Object Section -eq "Static Readiness" | Measure-Object Score -Sum).Sum
$staticMax = ($results | Where-Object Section -eq "Static Readiness" | Measure-Object MaxScore -Sum).Sum
$runtimeTotal = ($results | Where-Object Section -eq "Runtime Proof" | Measure-Object Score -Sum).Sum
$runtimeMax = ($results | Where-Object Section -eq "Runtime Proof" | Measure-Object MaxScore -Sum).Sum

$summary = [pscustomobject]@{
    ProjectRoot = $ProjectRoot
    BackendUrl = $BackendUrl
    StaticReadinessScore = $staticTotal
    StaticReadinessMax = $staticMax
    StaticReadinessPercent = [math]::Round(($staticTotal / $staticMax) * 100, 1)
    RuntimeProofScore = $runtimeTotal
    RuntimeProofMax = $runtimeMax
    RuntimeProofPercent = [math]::Round(($runtimeTotal / $runtimeMax) * 100, 1)
    OverallInterpretation = if ($runtimeTotal -le 4) { "Well-instrumented or partially prepared, but autonomy is not yet proven" } elseif ($runtimeTotal -le 9) { "Some live autonomy signals, but closed-loop self-healing is still only partially proven" } else { "Strong runtime evidence of bounded autonomy" }
    Timestamp = (Get-Date).ToString("s")
}

if ($Json) {
    [pscustomobject]@{
        Summary = $summary
        Results = $results
    } | ConvertTo-Json -Depth 6
    exit 0
}

Write-Host "=== AUTONOMY AUDIT V2 ===" -ForegroundColor Cyan
$summary | Format-List
Write-Host ""
Write-Host "--- Static Readiness ---" -ForegroundColor Yellow
$results | Where-Object Section -eq "Static Readiness" | Format-Table Area, Score, MaxScore, Evidence, Missing -AutoSize
Write-Host ""
Write-Host "--- Runtime Proof ---" -ForegroundColor Yellow
$results | Where-Object Section -eq "Runtime Proof" | Format-Table Area, Score, MaxScore, Evidence, Missing -AutoSize
