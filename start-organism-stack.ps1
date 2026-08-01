$ErrorActionPreference = "Stop"

$RepoRoot = "C:\Users\rober\Projects\v-horseshoe-v2"
$FrontendRoot = Join-Path $RepoRoot "organism-console"
$LogDir = Join-Path $RepoRoot "run-logs"

$env:QDRANT_URL = "http://127.0.0.1:6333"
$env:LLM_API_BASE = "http://127.0.0.1:8080"

$qdrantExe = "C:\Users\rober\Documents\v-horseshoe-sync\qdrant-bin\qdrant.exe"
if (-not (Test-Path $qdrantExe)) {
    $qdrantExe = "C:\Users\rober\.continue\v-horseshoe\qdrant-bin\qdrant.exe"
}
if (-not (Test-Path $qdrantExe)) {
    $qdrantExe = Join-Path $RepoRoot "qdrant-bin\qdrant.exe"
}

$qdrantOut = Join-Path $LogDir "qdrant.out.log"
$qdrantErr = Join-Path $LogDir "qdrant.err.log"
$backendOut = Join-Path $LogDir "backend.out.log"
$backendErr = Join-Path $LogDir "backend.err.log"
$frontendOut = Join-Path $LogDir "frontend.out.log"
$frontendErr = Join-Path $LogDir "frontend.err.log"

$chrome1 = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$chrome2 = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ensure-LogFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }
    else {
        Clear-Content -Path $Path -ErrorAction SilentlyContinue
    }
}

function Stop-ProcessesOnPort {
    param([int[]]$Ports)

    foreach ($port in $Ports) {
        try {
            $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
            foreach ($listener in $listeners) {
                try {
                    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
                }
                catch {}
            }
        }
        catch {}
    }
}

function Wait-ForTcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            $result = Test-NetConnection $HostName -Port $Port -WarningAction SilentlyContinue
            if ($result.TcpTestSucceeded) {
                return $true
            }
        }
        catch {}

        Start-Sleep -Seconds 1
    }

    return $false
}

function Wait-ForHttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                return $true
            }
        }
        catch {}

        Start-Sleep -Milliseconds 500
    }

    return $false
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Step "Cleaning old processes"
Stop-ProcessesOnPort -Ports @(6333, 8000, 5173)

Ensure-LogFile -Path $qdrantOut
Ensure-LogFile -Path $qdrantErr
Ensure-LogFile -Path $backendOut
Ensure-LogFile -Path $backendErr
Ensure-LogFile -Path $frontendOut
Ensure-LogFile -Path $frontendErr

Write-Step "Starting Qdrant"
Write-Host "Using: $qdrantExe"

$qdrantProc = Start-Process `
    -FilePath $qdrantExe `
    -WorkingDirectory (Split-Path $qdrantExe -Parent) `
    -RedirectStandardOutput $qdrantOut `
    -RedirectStandardError $qdrantErr `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Qdrant PID: $($qdrantProc.Id)"
Write-Host "Waiting for Qdrant readiness..."

if (-not (Wait-ForTcpPort -HostName "127.0.0.1" -Port 6333 -TimeoutSeconds 60)) {
    Write-Host "[FAIL] Qdrant did not become ready" -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Qdrant ready" -ForegroundColor Green

Write-Step "Starting backend"

$backendProc = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m","uvicorn","swarm_os.app.main:create_app","--factory","--host","127.0.0.1","--port","8000" `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $backendOut `
    -RedirectStandardError $backendErr `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host "Waiting for backend health..."

if (-not (Wait-ForTcpPort -HostName "127.0.0.1" -Port 8000 -TimeoutSeconds 60)) {
    Write-Host "[FAIL] Backend did not become healthy" -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Backend healthy" -ForegroundColor Green

Write-Step "Starting frontend"

$frontendPort = 5173

$frontendProc = Start-Process `
    -FilePath "npm.cmd" `
    -ArgumentList "run","dev","--","--host","127.0.0.1","--strictPort","--port","5173" `
    -WorkingDirectory $FrontendRoot `
    -RedirectStandardOutput $frontendOut `
    -RedirectStandardError $frontendErr `
    -WindowStyle Hidden `
    -PassThru

Write-Host "Frontend PID: $($frontendProc.Id)"
Write-Host "Waiting for frontend HTTP..."

if (-not (Wait-ForTcpPort -HostName "127.0.0.1" -Port $frontendPort -TimeoutSeconds 60)) {
    Write-Host "[FAIL] Frontend did not become reachable on port $frontendPort" -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Frontend reachable" -ForegroundColor Green

$frontendAlive = $false
try {
    $null = Get-Process -Id $frontendProc.Id -ErrorAction Stop
    $frontendAlive = $true
}
catch {
    $frontendAlive = $false
}

Write-Host ""
Write-Host "================ STACK READY ================" -ForegroundColor Green
Write-Host ("Qdrant PID  : {0}" -f $qdrantProc.Id)
Write-Host ("Backend PID : {0}" -f $backendProc.Id)
Write-Host ("Frontend PID: {0}" -f $frontendProc.Id)

Write-Host ""
Write-Host "Ports:"
Write-Host ("Qdrant  (6333) : {0}" -f (Test-NetConnection 127.0.0.1 -Port 6333 -WarningAction SilentlyContinue).TcpTestSucceeded)
Write-Host ("Backend (8000) : {0}" -f (Test-NetConnection 127.0.0.1 -Port 8000 -WarningAction SilentlyContinue).TcpTestSucceeded)
Write-Host ("Frontend(5173) : {0}" -f (Test-NetConnection 127.0.0.1 -Port 5173 -WarningAction SilentlyContinue).TcpTestSucceeded)
Write-Host ("Frontend PID alive: {0}" -f $frontendAlive)

Write-Host ""
Write-Host "Logs:"
Write-Host $qdrantOut
Write-Host $qdrantErr
Write-Host $backendOut
Write-Host $backendErr
Write-Host $frontendOut
Write-Host $frontendErr

$url = "http://127.0.0.1:$frontendPort/"

Write-Host ""
Write-Host "Frontend URL:"
Write-Host $url

Write-Host ""
Write-Host "============================================="

if (Wait-ForHttpReady -Url $url -TimeoutSeconds 30) {
    Start-Sleep -Seconds 1

    if (Test-Path $chrome1) {
        Start-Process -FilePath $chrome1 -ArgumentList "--new-window", $url
    }
    elseif (Test-Path $chrome2) {
        Start-Process -FilePath $chrome2 -ArgumentList "--new-window", $url
    }
    else {
        Start-Process $url
    }
}
else {
    Write-Host "[WARN] Frontend is running, but HTTP auto-open check did not pass in time." -ForegroundColor Yellow
}
