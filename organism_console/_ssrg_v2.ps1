function New-SSRG {
    $global:SSRG = @{
        events = New-Object System.Collections.Generic.List[object]
        file = ".ssrg_log.jsonl"
    }

    if (-not (Test-Path $global:SSRG.file)) {
        New-Item -ItemType File -Force $global:SSRG.file | Out-Null
    }

    Write-Host "=== SSRG LOCK v2 INITIALIZED ===" -ForegroundColor Cyan
}

function _Compute-SSRGHash {
    param($obj)

    $json = $obj | ConvertTo-Json -Depth 5 -Compress
    return ($json.GetHashCode())
}

function Add-SSRGEvent {
    param (
        [string]$from,
        [string]$to,
        [string]$type,
        [string]$payload = "",
        [string]$result = "",
        [float]$confidence = 1.0
    )

    if (-not $from -or -not $type) {
        Write-Host "[SSRG BLOCKED] invalid event schema" -ForegroundColor Red
        return
    }

    $event = @{
        id = [guid]::NewGuid().ToString()
        timestamp = (Get-Date).ToString("o")
        from = $from
        to = $to
        type = $type
        payload = $payload
        result = $result
        confidence = $confidence
    }

    $event.hash = _Compute-SSRGHash $event

    # in-memory store
    $global:SSRG.events.Add($event)

    # persistent append-only log
    $line = $event | ConvertTo-Json -Depth 5 -Compress
    Add-Content -Path $global:SSRG.file -Value $line

    Write-Host "[SSRG] $from → $to ($type)" -ForegroundColor DarkGreen
}

function Get-SSRGGraph {
    $nodes = @{}
    $edges = @{}

    foreach ($e in $global:SSRG.events) {
        if (-not $nodes.ContainsKey($e.from)) { $nodes[$e.from] = 0 }
        if (-not $nodes.ContainsKey($e.to)) { $nodes[$e.to] = 0 }

        $nodes[$e.from]++
        $nodes[$e.to]++

        $key = "$($e.from)->$($e.to)"
        if (-not $edges.ContainsKey($key)) {
            $edges[$key] = 0
        }
        $edges[$key]++
    }

    return @{
        nodes = $nodes
        edges = $edges
        totalEvents = $global:SSRG.events.Count
    }
}

function Show-SSRG {
    $g = Get-SSRGGraph

    Write-Host "`n=== SSRG NODES ===" -ForegroundColor Yellow
    $g.nodes.GetEnumerator() |
        Sort-Object Value -Descending |
        ForEach-Object { Write-Host "$($_.Key): $($_.Value)" }

    Write-Host "`n=== SSRG EDGES ===" -ForegroundColor Cyan
    $g.edges.GetEnumerator() |
        Sort-Object Value -Descending |
        ForEach-Object { Write-Host "$($_.Key) => $($_.Value)" }

    Write-Host "`nTotal Events: $($g.totalEvents)" -ForegroundColor Green
}

function Replay-SSRG {
    Write-Host "`n=== SSRG REPLAY ===" -ForegroundColor Magenta

    $log = Get-Content $global:SSRG.file | ConvertFrom-Json

    foreach ($e in $log) {
        Write-Host "$($e.from) → $($e.to) [$($e.type)]"
    }

    Write-Host "`nReplay complete: $($log.Count) events" -ForegroundColor Green
}
