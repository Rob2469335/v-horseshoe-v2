Set-Location "C:\Users\rober\Projects\v-horseshoe-v2"

$qdrantExe = "C:\Users\rober\.continue\v-horseshoe\qdrant-bin\qdrant.exe"
$qdrantHost = "127.0.0.1"
$qdrantPort = 6333
$qdrantGrpcPort = 6334

if (-not (Test-Path $qdrantExe)) {
    throw "Qdrant binary not found at: $qdrantExe"
}

$storageDir = "C:\Users\rober\Projects\v-horseshoe-v2\.qdrant\storage"
$configDir  = "C:\Users\rober\Projects\v-horseshoe-v2\.qdrant\config"
New-Item -ItemType Directory -Force -Path $storageDir, $configDir | Out-Null

$qdrantConfig = @"
log_level: INFO

service:
  host: 0.0.0.0
  http_port: $qdrantPort
  grpc_port: $qdrantGrpcPort

storage:
  storage_path: "$storageDir"
"@

$configPath = Join-Path $configDir "qdrant.yaml"
[System.IO.File]::WriteAllText($configPath, $qdrantConfig, [System.Text.UTF8Encoding]::new($false))

$qdrantAlreadyUp = $false
try {
    $qdrantAlreadyUp = Test-NetConnection $qdrantHost -Port $qdrantPort -InformationLevel Quiet -WarningAction SilentlyContinue
} catch {
    $qdrantAlreadyUp = $false
}

if (-not $qdrantAlreadyUp) {
    Write-Host "Starting Qdrant..." -ForegroundColor Cyan

    Start-Process `
        -FilePath $qdrantExe `
        -ArgumentList @("--config-path", $configPath) `
        -WorkingDirectory (Split-Path $qdrantExe -Parent)

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 750
        try {
            $qdrantAlreadyUp = Test-NetConnection $qdrantHost -Port $qdrantPort -InformationLevel Quiet -WarningAction SilentlyContinue
        } catch {
            $qdrantAlreadyUp = $false
        }
    } while ((-not $qdrantAlreadyUp) -and ((Get-Date) -lt $deadline))

    if (-not $qdrantAlreadyUp) {
        throw "Qdrant did not open port $qdrantPort within 30 seconds."
    }

    Write-Host "Qdrant is up on port $qdrantPort" -ForegroundColor Green
}
else {
    Write-Host "Qdrant is already running on port $qdrantPort" -ForegroundColor Yellow
}

$Env:PYTHONPATH = "C:\Users\rober\Projects\v-horseshoe-v2"
python -m uvicorn swarm_os.app.main:app --host 127.0.0.1 --port 8000 --reload