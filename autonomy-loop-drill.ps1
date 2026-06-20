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
$outDir = Join-Path (Get-Location) "autonomy-loop-drill-$timestamp"
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

$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$endAt = [TimeSpan]::FromMinutes($TotalMinutes)

$observations = @()
$cycle = 0

while ($stopwatch.Elapsed -lt $endAt) {
    $cycle++
    $target = Get-Process -Name $TargetProcessName -ErrorAction SilentlyContinue | Select-Object -First 1

    if ($target) {
        Write-Host ("Cycle {0}: stopping {1} (PID={2})" -f $cycle, $target.ProcessName, $target.Id) -ForegroundColor Yellow
        Stop-Process -Id $target.Id -Force
    } else {
        Write-Host ("Cycle {0}: target process not running, skipping stop" -f $cycle) -ForegroundColor Yellow
    }

    $half = [math]::Max(1, [math]::Floor($CycleSeconds / 2))
    for ($i = 0; $i -lt $half; $i += $PollSeconds) {
        $s = Get-JsonSafely "$BackendUrl/status"
        $h = Get-JsonSafely "$BackendUrl/health"
        $r = Get-JsonSafely "$BackendUrl/readyz"
        $t = Get-JsonSafely "$BackendUrl/tools"
        $tr = Get-JsonSafely "$BackendUrl/trace-summary"

        $observations += [pscustomobject]@{
            timestamp = (Get-Date).ToString("s")
            phase = "down"
            cycle = $cycle
            statusReachable = ($null -ne $s)
            healthReachable = ($null -ne $h)
            readyzReachable = ($null -ne $r)
            toolCount = Get-ToolCount -Tools $t -Status $s
            traceCount = Get-TraceCount -TraceSummary $tr
            recoverySignals = Get-RecoverySignalCount -Obj $s
        }
        Start-Sleep -Seconds $PollSeconds
        if ($stopwatch.Elapsed -ge $endAt) { break }
    }

    if ($stopwatch.Elapsed -ge $endAt) { break }

    Write-Host ("Cycle {0}: restarting with {1}" -f $cycle, $RestartCommand) -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit","-Command",$RestartCommand -PassThru | Out-Null

    for ($i = 0; $i -lt $half; $i += $PollSeconds) {
        $s = Get-JsonSafely "$BackendUrl/status"
        $h = Get-JsonSafely "$BackendUrl/health"
        $r = Get-JsonSafely "$BackendUrl/readyz"
        $t = Get-JsonSafely "$BackendUrl/tools"
        $tr = Get-JsonSafely "$BackendUrl/trace-summary"

        $observations += [pscustomobject]@{
            timestamp = (Get-Date).ToString("s")
            phase = "up"
            cycle = $cycle
            statusReachable = ($null -ne $s)
            healthReachable = ($null -ne $h)
            readyzReachable = ($null -ne $r)
            toolCount = Get-ToolCount -Tools $t -Status $s
            traceCount = Get-TraceCount -TraceSummary $tr
            recoverySignals = Get-RecoverySignalCount -Obj $s
        }
        Start-Sleep -Seconds $PollSeconds
        if ($stopwatch.Elapsed -ge $endAt) { break }
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

$beforeTraceCount = Get-TraceCount -TraceSummary $beforeTrace
$afterTraceCount = Get-TraceCount -TraceSummary $afterTrace
$beforeToolCount = Get-ToolCount -Tools $beforeTools -Status $beforeStatus
$afterToolCount = Get-ToolCount -Tools $afterTools -Status $afterStatus

$everDegraded = $false
$everRecovered = $false
$maxRecoverySignals = 0
if ($observations.Count -gt 0) {
    $maxRecoverySignals = ($observations | Measure-Object -Property recoverySignals -Maximum).Maximum
    $everDegraded = ($observations | Where-Object { -not $_.healthReachable -or -not $_.readyzReachable }).Count -gt 0
}
if (($null -ne $afterHealth) -and ($null -ne $afterReadyz)) { $everRecovered = $true }

$results = @()
$results += New-Row "Cycle automation" 2 2 ("cycles={0}; minutes={1}; interval={2}s" -f $cycle, $TotalMinutes, $CycleSeconds) "Automated loop should run through the full window"
$results += New-Row "Dependency fault injection" $(if($cycle -gt 0){2}else{0}) 2 ("targetProcess={0}" -f $TargetProcessName) "Need a real target process to stop and restart"
$results += New-Row "Degradation observed" $(if($everDegraded){2}else{0}) 2 ("everDegraded={0}" -f $everDegraded) "Need visible service degradation during the down phase"
$results += New-Row "Recovery observed" $(if($everRecovered){2}else{0}) 2 ("everRecovered={0}" -f $everRecovered) "Need service recovery after restart"
$results += New-Row "Recovery signals published" $(if($maxRecoverySignals -ge 1){2}else{0}) 2 ("maxRecoverySignals={0}" -f $maxRecoverySignals) "Need explicit degraded/recovering/recovered state publication"
$results += New-Row "Trace growth" $(if($afterTraceCount -gt $beforeTraceCount){2}else{0}) 2 ("beforeTraceCount={0}; afterTraceCount={1}" -f $beforeTraceCount, $afterTraceCount) "Need traces proving the response path"
$results += New-Row "Action surface survived" $(if($afterToolCount -ge 1){2}else{1}) 2 ("beforeToolCount={0}; afterToolCount={1}" -f $beforeToolCount, $afterToolCount) "Need resilient tool surface during repeated cycles"
$results += New-Row "Learning proof" 0 2 "Repeated loops still do not prove learning" "Need later runs showing improved handling"

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
    RuntimeDrillScore = $total
    RuntimeDrillMax = $max
    RuntimeDrillPercent = [math]::Round(($total / $max) * 100, 1)
    Interpretation = if ($total -le 8) { "Automated cycling works, but self-healing still needs proof" } elseif ($total -le 13) { "Strong automated recovery evidence" } else { "High confidence bounded autonomy evidence" }
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
Write-Host "=== AUTOMATED LOOP DRILL REPORT ===" -ForegroundColor Cyan
$summary | Format-List
Write-Host ""
$results | Format-Table Area, Score, MaxScore, Evidence, Missing -AutoSize
Write-Host ""
Write-Host "Artifacts saved to: $outDir" -ForegroundColor Green
