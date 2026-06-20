function New-SSRG {
    $global:SSRG = @{
        events = New-Object System.Collections.Generic.List[object]
    }

    Write-Host "=== SSRG INITIALIZED (Single Source Runtime Graph) ===" -ForegroundColor Cyan
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

    $event = @{
        id = [guid]::NewGuid().ToString()
        timestamp = Get-Date
        from = $from
        to = $to
        type = $type
        payload = $payload
        result = $result
        confidence = $confidence
    }

    $global:SSRG.events.Add($event)
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
        events = $global:SSRG.events.Count
    }
}

function Show-SSRG {
    $graph = Get-SSRGGraph

    Write-Host "`n=== SSRG NODE ACTIVITY ===" -ForegroundColor Yellow
    $graph.nodes.GetEnumerator() |
        Sort-Object Value -Descending |
        ForEach-Object { Write-Host "$($_.Key): $($_.Value)" }

    Write-Host "`n=== SSRG EDGE FLOW ===" -ForegroundColor Cyan
    $graph.edges.GetEnumerator() |
        Sort-Object Value -Descending |
        ForEach-Object { Write-Host "$($_.Key) => $($_.Value)" }

    Write-Host "`nTotal Events: $($graph.events)" -ForegroundColor Green
}
