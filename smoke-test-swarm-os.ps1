$ErrorActionPreference = "Stop"

$repoRoot     = "C:\Users\rober\Projects\v-horseshoe-v2"
$backendRoot  = "C:\Users\rober\Projects\v-horseshoe-v2"
$pythonExe    = "python"
$hostName     = "127.0.0.1"
$port         = 8011
$baseUrl      = "http://$hostName`:$port"
$logDir       = "C:\Users\rober\Projects\v-horseshoe-v2\run-logs"
$backendLog   = Join-Path $logDir "swarm-os-smoke-backend.log"
$backendErr   = Join-Path $logDir "swarm-os-smoke-backend.err.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Step($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Assert-True($condition, $message) {
    if (-not $condition) {
        throw $message
    }
}

function Assert-HasProperty($obj, $propertyName, $message) {
    $has = $null -ne ($obj.PSObject.Properties[$propertyName])
    if (-not $has) {
        throw $message
    }
}

function Wait-Port($hostValue, $portValue, $timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    do {
        if (Test-NetConnection -ComputerName $hostValue -Port $portValue -InformationLevel Quiet) {
            return $true
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Invoke-Json($url) {
    return Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 20
}

function Invoke-JsonPost($url, $body) {
    $json = $body | ConvertTo-Json -Depth 10
    return Invoke-RestMethod -Uri $url -Method Post -ContentType "application/json" -Body $json -TimeoutSec 30
}

Write-Step "Preflight compile"
Get-ChildItem $backendRoot -Recurse -File -Include "*.py" |
    Where-Object { $_.FullName -notlike "*\tests\*" } |
    ForEach-Object { & $pythonExe -m py_compile $_.FullName }

Write-Step "Kill old smoke backend on port $port"
Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
        try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {}
    }

Write-Step "Start backend"
Push-Location $backendRoot
$proc = Start-Process -FilePath $pythonExe `
    -ArgumentList "-m","uvicorn","swarm_os.app.main:create_app","--factory","--host",$hostName,"--port",$port `
    -WorkingDirectory $backendRoot `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErr `
    -PassThru
Pop-Location

Write-Host "Backend PID: $($proc.Id)" -ForegroundColor Yellow

try {
    Write-Step "Wait for port"
    Assert-True (Wait-Port -hostValue $hostName -portValue $port -timeoutSec 45) "Backend never opened port $port"

    Write-Step "Check process still alive"
    $runningProc = Get-Process -Id $proc.Id -ErrorAction Stop
    Assert-True ($null -ne $runningProc) "Backend process exited early"

    Write-Step "HTTP smoke - root health/status style probes"
    $probeResults = [ordered]@{}

    foreach ($url in @(
        "$baseUrl/docs",
        "$baseUrl/openapi.json",
        "$baseUrl/admin/status",
        "$baseUrl/admin/run-state",
        "$baseUrl/admin/snapshots",
        "$baseUrl/admin/dashboard",
        "$baseUrl/admin/generation"
    )) {
        try {
            $result = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 20
            $probeResults[$url] = @{ ok = $true; type = ($result.GetType().FullName) }
        } catch {
            $probeResults[$url] = @{ ok = $false; error = $_.Exception.Message }
        }
    }

    $probeResults.GetEnumerator() | ForEach-Object {
        if (-not $_.Value.ok) {
            throw "Endpoint failed: $($_.Key) :: $($_.Value.error)"
        }
    }

    Write-Step "Deep assertions"

    $status = Invoke-Json "$baseUrl/admin/status"
    Assert-HasProperty $status "status" "admin/status missing 'status'"
    Assert-True ($status.status -ne $null) "admin/status status is null"

    $runState = Invoke-Json "$baseUrl/admin/run-state"
    foreach ($name in @("latest_snapshot","queued","running")) {
        Assert-HasProperty $runState $name "admin/run-state missing '$name'"
    }

    $snapshots = Invoke-Json "$baseUrl/admin/snapshots"
    foreach ($name in @("count","snapshots")) {
        Assert-HasProperty $snapshots $name "admin/snapshots missing '$name'"
    }
    Assert-True ($snapshots.snapshots -is [System.Collections.IEnumerable]) "admin/snapshots snapshots is not enumerable"

    $dashboard = Invoke-Json "$baseUrl/admin/dashboard"
    foreach ($name in @("snapshot_count","latest_snapshot")) {
        Assert-HasProperty $dashboard $name "admin/dashboard missing '$name'"
    }

    $generation = Invoke-Json "$baseUrl/admin/generation"
    foreach ($name in @("latest_snapshot","current_run","population")) {
        Assert-HasProperty $generation $name "admin/generation missing '$name'"
    }

    Write-Step "Negative-path assertions"
    try {
        $resume = Invoke-RestMethod -Uri "$baseUrl/admin/resume-latest" -Method Post -TimeoutSec 20
        if ($null -eq $resume) {
            throw "resume-latest returned null"
        }
    } catch {
        $msg = $_.Exception.Message
        $allowed = @("404", "No snapshots found")
        $ok = $false
        foreach ($token in $allowed) {
            if ($msg -match [regex]::Escape($token)) { $ok = $true }
        }
        Assert-True $ok "resume-latest failed unexpectedly: $msg"
    }

    Write-Step "OpenAPI route presence"
    $openapi = Invoke-RestMethod -Uri "$baseUrl/openapi.json" -Method Get -TimeoutSec 20
    Assert-HasProperty $openapi "paths" "openapi.json missing paths"

    foreach ($route in @("/admin/status","/admin/run-state","/admin/snapshots","/admin/dashboard","/admin/generation")) {
        $present = $null -ne $openapi.paths.$route
        Assert-True $present "OpenAPI missing route $route"
    }

    Write-Step "Success summary"
    [pscustomobject]@{
        backend_pid = $proc.Id
        port = $port
        docs = "$baseUrl/docs"
        openapi = "$baseUrl/openapi.json"
        backend_log = $backendLog
        backend_err = $backendErr
        result = "PASS"
    } | Format-List
}
catch {
    Write-Host ""
    Write-Host "SMOKE TEST FAILED" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    Write-Host ""
    Write-Host "--- backend stdout tail ---" -ForegroundColor Yellow
    if (Test-Path $backendLog) { Get-Content $backendLog -Tail 80 }

    Write-Host ""
    Write-Host "--- backend stderr tail ---" -ForegroundColor Yellow
    if (Test-Path $backendErr) { Get-Content $backendErr -Tail 120 }

    throw
}
finally {
    Write-Step "Shutdown smoke backend"
    try {
        if ($proc -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $proc.Id -Force
        }
    } catch {}
}


