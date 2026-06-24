# start-dev.ps1
$ErrorActionPreference = "Continue"
$root = "C:\Users\rober\Projects\v-horseshoe-v2"

Write-Host "=== Swarm OS Unified Startup ===" -ForegroundColor Cyan

# Load .env into current session
$dotenvPath = Join-Path $root ".env"
if (Test-Path $dotenvPath) {
    Get-Content $dotenvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }
        if ($line -notmatch "=") { return }

        $name, $value = $line -split '=', 2
        $name = $name.Trim()
        $value = $value.Trim()

        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        Set-Item -Path "Env:$name" -Value $value
    }
}

# Normalize key names used by different parts of the app
if (-not $env:NVIDIA_API_KEY -and $env:NVIDIAAPIKEY) { $env:NVIDIA_API_KEY = $env:NVIDIAAPIKEY }
if (-not $env:NVIDIAAPIKEY -and $env:NVIDIA_API_KEY) { $env:NVIDIAAPIKEY = $env:NVIDIA_API_KEY }

if (-not $env:OPENROUTER_API_KEY -and $env:OPENROUTERAPIKEY) { $env:OPENROUTER_API_KEY = $env:OPENROUTERAPIKEY }
if (-not $env:OPENROUTERAPIKEY -and $env:OPENROUTER_API_KEY) { $env:OPENROUTERAPIKEY = $env:OPENROUTER_API_KEY }

Write-Host "NVIDIA key present:      $([bool]$env:NVIDIA_API_KEY)" -ForegroundColor Gray
Write-Host "OpenRouter key present:   $([bool]$env:OPENROUTER_API_KEY)" -ForegroundColor Gray
Write-Host "Gemini key present:       $([bool]$env:GEMINI_API_KEY)" -ForegroundColor Gray
Write-Host "Groq key present:         $([bool]$env:GROQ_API_KEY)" -ForegroundColor Gray
Write-Host "Tavily key present:       $([bool]$env:TAVILY_API_KEY)" -ForegroundColor Gray
Write-Host "Brave key present:        $([bool]$env:BRAVE_API_KEY)" -ForegroundColor Gray
Write-Host "Exa key present:          $([bool]$env:EXA_API_KEY)" -ForegroundColor Gray

# STEP 1 - Cleanup
Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("ollama","qdrant","node","python")) {
    Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "Cleanup complete ✔" -ForegroundColor Green

# STEP 2 - Ollama
Write-Host "`n[STEP 2] Starting Ollama..." -ForegroundColor Yellow
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 5
Write-Host "Ollama ✔" -ForegroundColor Green

# STEP 3 - Qdrant (wait until actually ready)
Write-Host "`n[STEP 3] Starting Qdrant..." -ForegroundColor Yellow
$qdrantPath = "C:\Users\rober\.continue\v-horseshoe\qdrant-bin\qdrant.exe"
Start-Process $qdrantPath -WindowStyle Hidden
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:6333" | Out-Null; Write-Host "Qdrant ✔" -ForegroundColor Green; break }
    catch { Write-Host "  waiting for Qdrant... ($i)" -ForegroundColor DarkGray; Start-Sleep 1 }
}

# STEP 4 - Backend (background job)
Write-Host "`n[STEP 4] Starting Backend..." -ForegroundColor Yellow
$env:PYTHONPATH = $root

$backendEnv = @{
    PYTHONPATH          = $root
    NVIDIA_API_KEY      = $env:NVIDIA_API_KEY
    NVIDIAAPIKEY        = $env:NVIDIAAPIKEY
    OPENROUTER_API_KEY  = $env:OPENROUTER_API_KEY
    OPENROUTERAPIKEY    = $env:OPENROUTERAPIKEY
    GEMINI_API_KEY      = $env:GEMINI_API_KEY
    GROQ_API_KEY        = $env:GROQ_API_KEY
    BRAVE_API_KEY       = $env:BRAVE_API_KEY
    TAVILY_API_KEY      = $env:TAVILY_API_KEY
    EXA_API_KEY         = $env:EXA_API_KEY
    SERPAPI_KEY         = $env:SERPAPI_KEY
    SERPER_API_KEY      = $env:SERPER_API_KEY
    TINYFISH_API_KEY    = $env:TINYFISH_API_KEY
    OPENROUTER_BASE_URL = $env:OPENROUTER_BASE_URL
    NVIDIA_BASE_URL     = $env:NVIDIA_BASE_URL
    SWARM_SEARXNG_URL   = $env:SWARM_SEARXNG_URL
}

$backendJob = Start-Job -ScriptBlock {
    param($r, $vars)
    Set-Location $r

    foreach ($k in $vars.Keys) {
        if ($null -ne $vars[$k] -and $vars[$k] -ne "") {
            Set-Item -Path "Env:$k" -Value $vars[$k]
        }
    }

    Write-Host "DEBUG root=$r"
Write-Host "DEBUG pwd=$(Get-Location)"
Write-Host "DEBUG PYTHONPATH=$env:PYTHONPATH"
& "C:\Python314\python.exe" -c "import os,sys,importlib; print('DEBUG cwd=', os.getcwd()); print('DEBUG sys.path[0]=', sys.path[0]); m=importlib.import_module('swarm_os.app.main'); print('DEBUG module=', m.__file__)"
& "C:\Python314\python.exe" -m uvicorn --app-dir $r swarm_os.app.main:app --host 127.0.0.1 --port 8000 2>&1
} -ArgumentList $root, $backendEnv

for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:8000/health" | Out-Null; break } catch { Start-Sleep 1 }
}
Write-Host "Backend ✔  http://127.0.0.1:8000" -ForegroundColor Green

# STEP 5 - Frontend (background job)
Write-Host "`n[STEP 5] Starting Frontend..." -ForegroundColor Yellow
$frontendJob = Start-Job -ScriptBlock {
    param($ui)
    Set-Location $ui
    & npm run dev -- --host 127.0.0.1 2>&1
} -ArgumentList "$root\organism-console"

for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:5173" | Out-Null; break } catch { Start-Sleep 1 }
}
Write-Host "Frontend ✔  http://127.0.0.1:5173" -ForegroundColor Green

Write-Host "`n=== All services up — streaming logs (Ctrl+C to stop) ===" -ForegroundColor Cyan
Write-Host "Backend:   http://127.0.0.1:8000" -ForegroundColor Gray
Write-Host "Qdrant:    http://127.0.0.1:6333" -ForegroundColor Gray
Write-Host "Ollama:    http://127.0.0.1:11434" -ForegroundColor Gray
Write-Host "Frontend:  http://127.0.0.1:5173" -ForegroundColor Gray
Write-Host ""

try {
    while ($true) {
        Receive-Job $backendJob  | ForEach-Object { Write-Host "[backend]  $_" -ForegroundColor DarkCyan }
        Receive-Job $frontendJob | ForEach-Object { Write-Host "[frontend] $_" -ForegroundColor DarkYellow }
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Red
    Stop-Job  $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    foreach ($svc in @("ollama","qdrant","node","python")) {
        Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

