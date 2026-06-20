param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$TargetProcessName = "ollama",
    [string]$RestartCommand = "ollama serve",
    [int]$TotalMinutes = 10,
    [int]$CycleSeconds = 30,
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

function Get-RecoverySignalCount {
    param($Obj)
    try {
        $json = $Obj | ConvertTo-Json -Depth 10
        $count = 0
        foreach ($pattern in @("degraded","recovering","recovered","fallback","retry","repair","incident","offline")) {
            if ($json -match $pattern) { $count++ }
        }
        return $count
    } catch {
        return 0
    }
}

function Test-Degraded {
    param($Health, $Readyz, $Status)
    if ($null -eq $Health -or $null -eq $Readyz) { return $true }
    $signals = Get-RecoverySignalCount -Obj $Status
    if ($signals -ge 1) { return $true }
    return $false
}

function Test-Recovered {
    param($Health, $Readyz)
    return (($null -ne $Health) -and ($null -ne $Readyz))
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
$outDir = Join-Path (Get-Location) "autonomy-loop-drill-v2-$timestamp"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$beforeStatus = Get-JsonSafely "$BackendUrl/status"
$beforeHealth = Get-JsonSafely "$BackendUrl/health"
$beforeReadyz = Get-JsonSafely "$BackendUrl/readyz"
$beforeTools = Get-JsonSafely "$BackendUrl/tools"
$beforeTrace = Get-JsonSafely "$BackendUrl/trace-summary"

[pscustomobject]@{
    status = $beforeStatus
    health = $beforeHealth
    readyz = $beforeReadyz
    tools = $beforeTools
    traceSummary = $beforeTrace
} | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "before.json") -Encoding utf8NoBOM

$overallWatch = [System.Diagnostics.Stopwatch]::StartNew()
$endAt = [TimeSpan]::FromMinutes($TotalMinutes)

$observations = @()
$cycleMetrics = @()
$cycle = 0

while ($overallWatch.Elapsed -lt $endAt) {
    $cycle++
    $cycleWatch = [System.Diagnostics.Stopwatch]::StartNew()

    $target = Get-Process -Name $TargetProcessName -ErrorAction SilentlyContinue | Select-Object -First 1
    $targetFound = ($null -ne $target)
    $degradedObserved = $false
    $recoveredObserved = $false
    $recoverySignalObserved = $false
    $firstDegradedAtMs = $null
    $recoveredAtMs = $null
    $maxRecoverySignals = 0

    if ($targetFound) {
        Write-Host ("Cycle {0}: stopping {1} (PID={2})" -f $cycle, $target.ProcessName, $target.Id) -ForegroundColor Yellow
        Stop-Process -Id $target.Id -Force
    } else {
        Write-Host ("Cycle {0}: target process not found" -f $cycle) -ForegroundColor Yellow
    }

    $half = [math]::Max(1, [math]::Floor($CycleSeconds / 2))

    for ($i = 0; $i -lt $half; $i += $PollSeconds) {
        $s = Get-JsonSafely "$BackendUrl/status"
        $h = Get-JsonSafely "$BackendUrl/health"
        $r = Get-JsonSafely "$BackendUrl/readyz"
        $t = Get-JsonSafely "$BackendUrl/tools"
        $tr = Get-JsonSafely "$BackendUrl/trace-summary"

        $signals = Get-RecoverySignalCount -Obj $s
        if ($signals -gt $maxRecoverySignals) { $maxRecoverySignals = $signals }
        if ($signals -ge 1) { $recoverySignalObserved = $true }

        if ((Test-Degraded -Health $h -Readyz $r -Status $s) -and -not $degradedObserved) {
            $degradedObserved = $true
            $firstDegradedAtMs = $cycleWatch.ElapsedMilliseconds
        }

        $obs = [pscustomobject]@{
            timestamp = (Get-Date).ToString("s")
            cycle = $cycle
            phase = "down"
            elapsedMs = $cycleWatch.ElapsedMilliseconds
            statusReachable = ($null -ne $s)
            healthReachable = ($null -ne $h)
            readyzReachable = ($null -ne $r)
            toolCount = Get-ToolCount -Tools $t -Status $s
            traceCount = Get-TraceCount -TraceSummary $tr
            recoverySignals = $signals
        }
        $observations += $obs

        Start-Sleep -Seconds $PollSeconds
        if ($overallWatch.Elapsed -ge $endAt) { break }
    }

    if ($overallWatch.Elapsed -ge $endAt) { break }

    Write-Host ("Cycle {0}: restarting with {1}" -f $cycle, $RestartCommand) -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit","-Command",$RestartCommand -PassThru | Out-Null

    for ($i = 0; $i -lt $half; $i += $PollSeconds) {
        $s = Get-JsonSafely "$BackendUrl/status"
        $h = Get-JsonSafely "$BackendUrl/health"
        $r = Get-JsonSafely "$BackendUrl/readyz"
        $t = Get-JsonSafely "$BackendUrl/tools"
        $tr = Get-JsonSafely "$BackendUrl/trace-summary"

        $signals = Get-RecoverySignalCount -Obj $s
        if ($signals -gt $maxRecoverySignals) { $maxRecoverySignals = $signals }
        if ($signals -ge 1) { $recoverySignalObserved = $true }

        if ((Test-Degraded -Health $h -Readyz $r -Status $s) -and -not $degradedObserved) {
            $degradedObserved = $true
            $firstDegradedAtMs = $cycleWatch.ElapsedMilliseconds
        }

        if ((Test-Recovered -Health $h -Readyz $r) -and -not $recoveredObserved) {
            $recoveredObserved = $true
            $recoveredAtMs = $cycleWatch.ElapsedMilliseconds
        }

        $obs = [pscustomobject]@{
            timestamp = (Get-Date).ToString("s")
            cycle = $cycle
            phase = "up"
            elapsedMs = $cycleWatch.ElapsedMilliseconds
            statusReachable = ($null -ne $s)
            healthReachable = ($null -ne $h)
            readyzReachable = ($null -ne $r)
            toolCount = Get-ToolCount -Tools $t -Status $s
            traceCount = Get-TraceCount -TraceSummary $tr
            recoverySignals = $signals
        }
        $observations += $obs

        Start-Sleep -Seconds $PollSeconds
        if ($overallWatch.Elapsed -ge $endAt) { break }
    }

    $responseLatencyMs = if ($null -ne $firstDegradedAtMs) { $firstDegradedAtMs } else { -1 }
    $recoveryLatencyMs = if ($null -ne $recoveredAtMs) { $recoveredAtMs } else { -1 }

    $cycleMetrics += [pscustomobject]@{
        cycle = $cycle
        targetFound = $targetFound
        degradedObserved = $degradedObserved
        recoveredObserved = $recoveredObserved
        recoverySignalObserved = $recoverySignalObserved
        responseLatencyMs = $responseLatencyMs
        recoveryLatencyMs = $recoveryLatencyMs
        maxRecoverySignals = $maxRecoverySignals
    }
}

$afterStatus = Get-JsonSafely "$BackendUrl/status"
$afterHealth = Get-JsonSafely "$BackendUrl/health"
$afterReadyz = Get-JsonSafely "$BackendUrl/readyz"
$afterTools = Get-JsonSafely "$BackendUrl/tools"
$afterTrace = Get-JsonSafely "$BackendUrl/trace-summary"

[pscustomobject]@{
    status = $afterStatus
    health = $afterHealth
    readyz = $afterReadyz
    tools = $afterTools
    traceSummary = $afterTrace
} | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "after.json") -Encoding utf8NoBOM

$observations | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "observations.json") -Encoding utf8NoBOM
$cycleMetrics | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $outDir "cycle-metrics.json") -Encoding utf8NoBOM
$cycleMetrics | Export-Csv -Path (Join-Path $outDir "cycle-metrics.csv") -NoTypeInformation -Encoding utf8

$beforeTraceCount = Get-TraceCount -TraceSummary $beforeTrace
$afterTraceCount = Get-TraceCount -TraceSummary $afterTrace
$beforeToolCount = Get-ToolCount -Tools $beforeTools -Status $beforeStatus
$afterToolCount = Get-ToolCount -Tools $afterTools -Status $afterStatus

$successfulResponses = @($cycleMetrics | Where-Object { $_.responseLatencyMs -ge 0 })
$successfulRecoveries = @($cycleMetrics | Where-Object { $_.recoveryLatencyMs -ge 0 })

$avgResponseLatencyMs = if ($successfulResponses.Count -gt 0) { [math]::Round((($successfulResponses | Measure-Object -Property responseLatencyMs -Average).Average), 1) } else { -1 }
$avgRecoveryLatencyMs = if ($successfulRecoveries.Count -gt 0) { [math]::Round((($successfulRecoveries | Measure-Object -Property recoveryLatencyMs -Average).Average), 1) } else { -1 }

$firstRecovery = $successfulRecoveries | Select-Object -First 1
$lastRecovery = $successfulRecoveries | Select-Object -Last 1
$improvedRecovery = $false
if ($firstRecovery -and $lastRecovery -and $firstRecovery.recoveryLatencyMs -gt 0 -and $lastRecovery.recoveryLatencyMs -gt 0) {
    $improvedRecovery = ($lastRecovery.recoveryLatencyMs -lt $firstRecovery.recoveryLatencyMs)
}

$degradedCycles = @($cycleMetrics | Where-Object { $_.degradedObserved }).Count
$recoveredCycles = @($cycleMetrics | Where-Object { $_.recoveredObserved }).Count
$signalCycles = @($cycleMetrics | Where-Object { $_.recoverySignalObserved }).Count

$results = @()
$results += New-Row "Cycle automation" 2 2 ("cycles={0}; minutes={1}; interval={2}s" -f $cycle, $TotalMinutes, $CycleSeconds) "Automated loop should run through the full window"
$results += New-Row "Dependency fault injection" $(if($cycleMetrics.Count -gt 0 -and (@($cycleMetrics | Where-Object {$_.targetFound}).Count -ge 1)){2}else{0}) 2 ("targetProcess={0}" -f $TargetProcessName) "Need a real target process to stop and restart"
$results += New-Row "Degradation observed" $(if($degradedCycles -ge 1){2}else{0}) 2 ("degradedCycles={0}/{1}; avgResponseLatencyMs={2}" -f $degradedCycles, $cycleMetrics.Count, $avgResponseLatencyMs) "Need visible service degradation during the down phase"
$results += New-Row "Recovery observed" $(if($recoveredCycles -ge 1){2}else{0}) 2 ("recoveredCycles={0}/{1}; avgRecoveryLatencyMs={2}" -f $recoveredCycles, $cycleMetrics.Count, $avgRecoveryLatencyMs) "Need service recovery after restart"
$results += New-Row "Recovery signals published" $(if($signalCycles -ge 1){2}else{0}) 2 ("signalCycles={0}/{1}" -f $signalCycles, $cycleMetrics.Count) "Need explicit degraded/recovering/recovered state publication"
$results += New-Row "Trace growth" $(if($afterTraceCount -gt $beforeTraceCount){2}else{0}) 2 ("beforeTraceCount={0}; afterTraceCount={1}" -f $beforeTraceCount, $afterTraceCount) "Need traces proving the response path"
$results += New-Row "Action surface survived" $(if($afterToolCount -ge 1){2}else{1}) 2 ("beforeToolCount={0}; afterToolCount={1}" -f $beforeToolCount, $afterToolCount) "Need resilient tool surface during repeated cycles"
$results += New-Row "Learning trend proxy" $(if($improvedRecovery){1}else{0}) 2 ("firstRecoveryMs={0}; lastRecoveryMs={1}; improved={2}" -f $(if($firstRecovery){$firstRecovery.recoveryLatencyMs}else{-1}), $(if($lastRecovery){$lastRecovery.recoveryLatencyMs}else{-1}), $improvedRecovery) "Need repeated runs with clearly improving recovery behavior"

$total = ($results | Measure-Object -Property Score -Sum).Sum
$max = ($results | Measure-Object -Property MaxScore -Sum).Sum

$summary = [pscustomobject]@{
    BackendUrl = $BackendUrl
    TargetProcessName = $TargetProcessName
    RestartCommand = $RestartCommand
    TotalMinutes = $TotalMinutes
    CycleSeconds = $CycleSeconds
    PollSeconds = $PollSeconds
    OutputDirectory = $outDir
    TotalCycles = $cycleMetrics.Count
    AvgResponseLatencyMs = $avgResponseLatencyMs
    AvgRecoveryLatencyMs = $avgRecoveryLatencyMs
    ImprovedRecoveryTrend = $improvedRecovery
    RuntimeDrillScore = $total
    RuntimeDrillMax = $max
    RuntimeDrillPercent = [math]::Round(($total / $max) * 100, 1)
    Interpretation = if ($total -le 8) { "Automated cycling works, but self-healing still needs stronger proof" } elseif ($total -le 13) { "Strong automated recovery evidence with partial observability" } else { "High confidence bounded autonomy evidence" }
    Timestamp = (Get-Date).ToString("s")
}

if ($Json) {
    [pscustomobject]@{
        Summary = $summary
        Results = $results
        CycleMetrics = $cycleMetrics
    } | ConvertTo-Json -Depth 8
    exit 0
}

Write-Host ""
Write-Host "=== AUTOMATED LOOP DRILL REPORT V2 ===" -ForegroundColor Cyan
$summary | Format-List
Write-Host ""
$results | Format-Table Area, Score, MaxScore, Evidence, Missing -AutoSize
Write-Host ""
Write-Host "--- Per-cycle metrics ---" -ForegroundColor Yellow
$cycleMetrics | Format-Table cycle, targetFound, degradedObserved, recoveredObserved, recoverySignalObserved, responseLatencyMs, recoveryLatencyMs, maxRecoverySignals -AutoSize
Write-Host ""
Write-Host "Artifacts saved to: $outDir" -ForegroundColor Green
