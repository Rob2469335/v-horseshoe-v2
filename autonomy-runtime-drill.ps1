param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [int]$ObserveSeconds = 90,
    [int]$PollSeconds = 5,
    [switch]$Json
)

$ErrorActionPreference = "SilentlyContinue"

function Get-JsonSafely {
    param([string]$Url)
    try { Invoke-RestMethod $Url } catch { $null }
}

function Get-TraceCount {
    param($TraceSummary)
    try {
        if ($TraceSummary -is [System.Array]) { return @($TraceSummary).Count }
        if ($TraceSummary.traces) { return @($TraceSummary.traces).Count }
        if ($TraceSummary.items) { return @($TraceSummary.items).Count }
    } catch {}
    return 0
}

function Get-ToolCount {
    param($Tools, $Status)
    try {
        if ($Tools.tools) { return @($Tools.tools).Count }
        if ($Status.tools) { return @($Status.tools).Count }
        if ($Status.capabilities.tools.count) { return [int]$Status.capabilities.tools.count }
    } catch {}
    return 0
}

function Get-VisionAvailable {
    param($Status)
    try { return [bool]($Status.capabilities.vision.available) } catch { return $false }
}

function Get-ManualOnly {
    param($Status)
    try { return $Status.capabilities.manual_only } catch { return $null }
}

function Get-RecoverySignalCount {
    param($Obj)
    try {
        $json = $Obj | ConvertTo-Json -Depth 10
        $count = 0
        foreach ($pattern in @("degraded","recovering","recovered","fallback","retry","repair","incident")) {
            if ($json -match $pattern) { $count++ }
        }
        return $count
    } catch {
        return 0
    }
}

function New-Row {
    param(
        [string]$Area,
        [int]$Score,
        [int]$MaxScore,
        [string]$Evidence,
        [string]$Missing
    )
    [pscustomobject]@{
        Area     = $Area
        Score    = $Score
        MaxScore = $MaxScore
        Evidence = $Evidence
        Missing  = $Missing
    }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path (Get-Location) "autonomy-drill-$timestamp"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Host "Capturing baseline..." -ForegroundColor Cyan
$beforeStatus = Get-JsonSafely "$BackendUrl/status"
$beforeHealth = Get-JsonSafely "$BackendUrl/health"
$beforeReadyz = Get-JsonSafely "$BackendUrl/readyz"
$beforeTools = Get-JsonSafely "$BackendUrl/tools"
$beforeTrace = Get-JsonSafely "$BackendUrl/trace-summary"

$before = [pscustomobject]@{
    status = $beforeStatus
    health = $beforeHealth
    readyz = $beforeReadyz
    tools = $beforeTools
    traceSummary = $beforeTrace
}
$before | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "before.json") -Encoding utf8NoBOM

Write-Host ""
Write-Host "Now trigger your drill manually during the next $ObserveSeconds seconds." -ForegroundColor Yellow
Write-Host "Examples: stop a dependency, force a degraded mode, disable a model endpoint, or provoke a known recoverable fault." -ForegroundColor Yellow
Write-Host ""

$observations = @()
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

while ($stopwatch.Elapsed.TotalSeconds -lt $ObserveSeconds) {
    $s = Get-JsonSafely "$BackendUrl/status"
    $h = Get-JsonSafely "$BackendUrl/health"
    $r = Get-JsonSafely "$BackendUrl/readyz"
    $t = Get-JsonSafely "$BackendUrl/tools"
    $tr = Get-JsonSafely "$BackendUrl/trace-summary"

    $obs = [pscustomobject]@{
        timestamp = (Get-Date).ToString("s")
        backendReachable = ($null -ne $s)
        healthReachable = ($null -ne $h)
        readyzReachable = ($null -ne $r)
        toolCount = Get-ToolCount -Tools $t -Status $s
        traceCount = Get-TraceCount -TraceSummary $tr
        visionAvailable = Get-VisionAvailable -Status $s
        manualOnly = Get-ManualOnly -Status $s
        recoverySignals = Get-RecoverySignalCount -Obj $s
        status = $s
    }

    $observations += $obs

    Write-Host ("[{0}] reachable={1} tools={2} traces={3} vision={4} manualOnly={5} recoverySignals={6}" -f `
        $obs.timestamp, $obs.backendReachable, $obs.toolCount, $obs.traceCount, $obs.visionAvailable, $obs.manualOnly, $obs.recoverySignals)

    Start-Sleep -Seconds $PollSeconds
}

Write-Host ""
Write-Host "Capturing final state..." -ForegroundColor Cyan
$afterStatus = Get-JsonSafely "$BackendUrl/status"
$afterHealth = Get-JsonSafely "$BackendUrl/health"
$afterReadyz = Get-JsonSafely "$BackendUrl/readyz"
$afterTools = Get-JsonSafely "$BackendUrl/tools"
$afterTrace = Get-JsonSafely "$BackendUrl/trace-summary"

$after = [pscustomobject]@{
    status = $afterStatus
    health = $afterHealth
    readyz = $afterReadyz
    tools = $afterTools
    traceSummary = $afterTrace
}
$after | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "after.json") -Encoding utf8NoBOM
$observations | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "observations.json") -Encoding utf8NoBOM

$beforeTraceCount = Get-TraceCount -TraceSummary $beforeTrace
$afterTraceCount = Get-TraceCount -TraceSummary $afterTrace
$beforeToolCount = Get-ToolCount -Tools $beforeTools -Status $beforeStatus
$afterToolCount = Get-ToolCount -Tools $afterTools -Status $afterStatus

$maxRecoverySignals = 0
if ($observations.Count -gt 0) {
    $maxRecoverySignals = ($observations | Measure-Object -Property recoverySignals -Maximum).Maximum
}

$everUnhealthy = $false
$everRecovered = $false
foreach ($o in $observations) {
    if (-not $o.healthReachable -or -not $o.readyzReachable) { $everUnhealthy = $true }
}
if (($null -ne $afterHealth) -and ($null -ne $afterReadyz)) { $everRecovered = $true }

$results = @()

$liveReachScore = if (($null -ne $beforeStatus) -and ($null -ne $afterStatus)) { 2 } elseif (($null -ne $beforeStatus) -or ($null -ne $afterStatus)) { 1 } else { 0 }
$results += New-Row "Live endpoint continuity" $liveReachScore 2 ("beforeStatus={0}; afterStatus={1}" -f ($null -ne $beforeStatus), ($null -ne $afterStatus)) "Need stable live endpoints throughout the drill"

$toolScore = if ($afterToolCount -ge 3) { 2 } elseif ($afterToolCount -ge 1) { 1 } else { 0 }
$results += New-Row "Action surface during drill" $toolScore 2 ("beforeTools={0}; afterTools={1}" -f $beforeToolCount, $afterToolCount) "Need enough live tools for meaningful autonomous response"

$traceScore = if ($afterTraceCount -gt $beforeTraceCount) { 2 } elseif ($afterTraceCount -ge 1) { 1 } else { 0 }
$results += New-Row "Trace growth during drill" $traceScore 2 ("beforeTraceCount={0}; afterTraceCount={1}" -f $beforeTraceCount, $afterTraceCount) "Need traces that show action/outcome progress during the drill"

$recoverySignalScore = if ($maxRecoverySignals -ge 2) { 2 } elseif ($maxRecoverySignals -ge 1) { 1 } else { 0 }
$results += New-Row "Published degradation/recovery signals" $recoverySignalScore 2 ("maxRecoverySignals={0}" -f $maxRecoverySignals) "Need explicit degraded/recovering/recovered signals in live status"

$validationScore = 0
if ($everUnhealthy) { $validationScore++ }
if ($everRecovered) { $validationScore++ }
$results += New-Row "Observed recovery cycle" $validationScore 2 ("everUnhealthy={0}; everRecovered={1}" -f $everUnhealthy, $everRecovered) "Need a visible unhealthy-to-recovered cycle during the drill"

$capabilityScore = 0
if (Get-VisionAvailable -Status $afterStatus) { $capabilityScore++ }
if ((Get-ManualOnly -Status $afterStatus) -eq $false) { $capabilityScore++ }
$results += New-Row "Autonomy exposure after drill" $capabilityScore 2 ("visionAvailable={0}; manualOnly={1}" -f (Get-VisionAvailable -Status $afterStatus), (Get-ManualOnly -Status $afterStatus)) "Need stronger non-manual live capability exposure"

$learningScore = 0
$results += New-Row "Learning proof across repeated drills" $learningScore 2 "Single drill cannot prove learning" "Need repeated drills showing better future handling from prior incidents"

$total = ($results | Measure-Object -Property Score -Sum).Sum
$max = ($results | Measure-Object -Property MaxScore -Sum).Sum

$summary = [pscustomobject]@{
    BackendUrl = $BackendUrl
    ObserveSeconds = $ObserveSeconds
    PollSeconds = $PollSeconds
    OutputDirectory = $outDir
    RuntimeDrillScore = $total
    RuntimeDrillMax = $max
    RuntimeDrillPercent = [math]::Round(($total / $max) * 100, 1)
    Interpretation = if ($total -le 4) { "Little proof of closed-loop autonomy in this drill" } elseif ($total -le 9) { "Partial live autonomy proof; self-healing still incomplete" } else { "Strong bounded autonomy evidence in this drill" }
    Timestamp = (Get-Date).ToString("s")
}

if ($Json) {
    [pscustomobject]@{
        Summary = $summary
        Results = $results
    } | ConvertTo-Json -Depth 8
    exit 0
}

Write-Host ""
Write-Host "=== RUNTIME DRILL REPORT ===" -ForegroundColor Cyan
$summary | Format-List
Write-Host ""
$results | Format-Table Area, Score, MaxScore, Evidence, Missing -AutoSize
Write-Host ""
Write-Host "Artifacts saved to: $outDir" -ForegroundColor Green
