# start-dev.ps1
$ErrorActionPreference = "Continue"
$root = "C:\Users\rober\Projects\v-horseshoe-v2"

Write-Host "=== Swarm OS Unified Startup ===" -ForegroundColor Cyan

# --- Duplicate Process Detection ---
# Check for existing processes listening on key ports or child processes
Write-Host "`n[PRE-CHECK] Checking for existing instances..." -ForegroundColor Yellow

# Check backend port (8000)
$backendPort = 8000
$backendProcesses = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
if ($backendProcesses) {
    Write-Host "WARNING: A process is already listening on port $backendPort. Exiting to prevent duplicates." -ForegroundColor Red
    exit 1
} else {
    Write-Host "  Port $backendPort is available." -ForegroundColor Green
}

# Check Whisper server port (8001)
$whisperPort = 8001
$whisperProcesses = Get-NetTCPConnection -LocalPort $whisperPort -State Listen -ErrorAction SilentlyContinue
if ($whisperProcesses) {
    Write-Host "WARNING: A process is already listening on port $whisperPort. Exiting to prevent duplicates." -ForegroundColor Red
    exit 1
} else {
    Write-Host "  Port $whisperPort is available." -ForegroundColor Green
}

# Check for running ambient_listener.py processes
# This command might need adjustment based on how python processes are invoked (e.g., full path vs. 'python')
# This assumes the process command line includes ambient_listener.py
$listenerProcesses = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*ambient_listener.py*' }
if ($listenerProcesses) {
    Write-Host "WARNING: An ambient_listener.py process is already running. Exiting to prevent duplicates." -ForegroundColor Red
    # Optionally, display PIDs of duplicates:
    # $listenerProcesses | ForEach-Object { Write-Host "  - PID: $($_.Id)" -ForegroundColor Gray }
    exit 1
} else {
    Write-Host "  No ambient_listener.py process detected." -ForegroundColor Green
}

# --- End Duplicate Process Detection ---


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
Write-Host "Serper key present:       $([bool]$env:SERPER_API_KEY)" -ForegroundColor Gray
Write-Host "SerpApi key present:      $([bool]$env:SERPAPI_KEY)" -ForegroundColor Gray
Write-Host "TinyFish key present:     $([bool]$env:TINYFISH_API_KEY)" -ForegroundColor Gray

# STEP 1 - Cleanup
Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("ollama","qdrant","node","python")) {
    Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "Cleanup complete ✔" -ForegroundColor Green

# STEP 2 - Ollama
Write-Host "`n[STEP 2] Starting Ollama..." -ForegroundColor Yellow
# Set env vars at process level so child processes inherit them
$env:OLLAMA_MAX_LOADED_MODELS = "2"
$env:OLLAMA_VULKAN = "1"
$env:OLLAMA_IGPU_ENABLE = "1"
$env:OLLAMA_CONTEXT_LENGTH = "8192"
$env:OLLAMA_KEEP_ALIVE = "-1"
$env:OLLAMA_FLASH_ATTENTION = "true"
$env:SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS = "1"
$env:SYCL_CACHE_PERSISTENT = "1"
$env:ONEAPI_DEVICE_SELECTOR = "level_zero:0"

# Start-Process does NOT inherit $env vars. Use Start-Job or direct invocation instead.
$ollamaJob = Start-Job -ScriptBlock {
    $env:OLLAMA_MAX_LOADED_MODELS = "2"
    $env:OLLAMA_VULKAN = "1"
    $env:OLLAMA_IGPU_ENABLE = "1"
    $env:OLLAMA_CONTEXT_LENGTH = "8192"
    $env:OLLAMA_KEEP_ALIVE = "-1"
    $env:OLLAMA_FLASH_ATTENTION = "false"
    $env:OLLAMA_NOMMAP = "1"
    $env:SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS = "1"
    $env:SYCL_CACHE_PERSISTENT = "1"
    $env:ONEAPI_DEVICE_SELECTOR = "level_zero:0"
    $env:GGML_VK_ALLOW_GRAPHICS_QUEUE = "1"
    & ollama serve 2>&1
}
Start-Sleep -Seconds 5
Get-Process -Name ollama -ErrorAction SilentlyContinue | ForEach-Object { 
    $_.PriorityClass = 'High'
    try { $_.ProcessorAffinity = 255 } catch {}
}
Write-Host "Ollama ✔ (Priority: High, Affinity: P-Cores Only)" -ForegroundColor Green

# STEP 3 - Qdrant (wait until actually ready)
Write-Host "`n[STEP 3] Starting Qdrant..." -ForegroundColor Yellow
$qdrantPath = "$root\qdrant-bin\qdrant.exe"
if (-not (Test-Path $qdrantPath)) {
    $qdrantPath = "C:\Users\rober\.continue\v-horseshoe\qdrant-bin\qdrant.exe"
}
if (-not (Test-Path $qdrantPath)) {
    $qdrantPath = "qdrant"
}
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
    NVIDIA_NIM_API_KEY  = $env:NVIDIA_API_KEY
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
    $pythonPath = if (Test-Path "$r\.venv\Scripts\python.exe") { "$r\.venv\Scripts\python.exe" } else { "python" }
    & $pythonPath -c "import os,sys,importlib; print('DEBUG cwd=', os.getcwd()); print('DEBUG sys.path[0]=', sys.path[0]); m=importlib.import_module('swarm_os.app.main'); print('DEBUG module=', m.__file__)"
    & $pythonPath -m uvicorn --app-dir $r swarm_os.app.main:app --host 127.0.0.1 --port 8000 2>&1
} -ArgumentList $root, $backendEnv

for ($i = 0; $i -lt 20; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:8000/health" | Out-Null; break } catch { Start-Sleep 1 }
}
Write-Host "Backend ✔  http://127.0.0.1:8000" -ForegroundColor Green

# STEP 4.5 - MCP Servers
Write-Host "`n[STEP 4.5] Starting MCP Servers..." -ForegroundColor Yellow
for ($i = 0; $i -lt 20; $i++) {
    try {
        $tools = Invoke-RestMethod "http://127.0.0.1:8000/tools"
        if ($tools.count -gt 0) {
            break
        }
        Start-Sleep 1
    } catch {
        Start-Sleep 1
    }
}
Write-Host "MCP Servers ✔  Registered $($tools.count) tools" -ForegroundColor Green

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

# STEP 6 - Whisper Server (background job)
Write-Host "`n[STEP 6] Starting Whisper Server..." -ForegroundColor Yellow
$whisperJob = Start-Job -ScriptBlock {
    param($r)
    Set-Location $r
    $pythonPath = if (Test-Path "$r\.venv\Scripts\python.exe") { "$r\.venv\Scripts\python.exe" } else { "python" }
    & $pythonPath whisper_server.py 2>&1
} -ArgumentList $root

for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "http://127.0.0.1:8001/docs" | Out-Null; break } catch { Start-Sleep 1 }
}
Write-Host "Whisper Server ✔  http://127.0.0.1:8001" -ForegroundColor Green

# STEP 7 - Ambient Listener (background job)
Write-Host "`n[STEP 7] Starting Ambient Listener..." -ForegroundColor Yellow
$listenerJob = Start-Job -ScriptBlock {
    param($r)
    Set-Location $r
    $pythonPath = if (Test-Path "$r\.venv\Scripts\python.exe") { "$r\.venv\Scripts\python.exe" } else { "python" }
    & $pythonPath ambient_listener.py 2>&1
} -ArgumentList $root

Start-Sleep -Seconds 5
Write-Host "Ambient Listener ✔" -ForegroundColor Green

Write-Host "`n=== All services up — streaming logs (Ctrl+C to stop) ===" -ForegroundColor Cyan
Write-Host "Backend:   http://127.0.0.1:8000" -ForegroundColor Gray
Write-Host "Qdrant:    http://127.0.0.1:6333" -ForegroundColor Gray
Write-Host "Ollama:    http://127.0.0.1:11434" -ForegroundColor Gray
Write-Host "Frontend:  http://127.0.0.1:5173" -ForegroundColor Gray
Write-Host "Whisper:   http://127.0.0.1:8001" -ForegroundColor Gray
Write-Host "Listener:  Active" -ForegroundColor Gray
Write-Host ""

try {
    while ($true) {
        Receive-Job $ollamaJob   | ForEach-Object { Write-Host "[ollama]   $_" -ForegroundColor DarkGreen }
        Receive-Job $backendJob  | ForEach-Object { Write-Host "[backend]  $_" -ForegroundColor DarkCyan }
        Receive-Job $frontendJob | ForEach-Object { Write-Host "[frontend] $_" -ForegroundColor DarkYellow }
        Receive-Job $whisperJob  | ForEach-Object { Write-Host "[whisper]  $_" -ForegroundColor DarkMagenta }
        Receive-Job $listenerJob | ForEach-Object { Write-Host "[listener] $_" -ForegroundColor DarkYellow }
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Red
    Stop-Job  $ollamaJob, $backendJob, $frontendJob, $whisperJob, $listenerJob -ErrorAction SilentlyContinue
    Remove-Job $ollamaJob, $backendJob, $frontendJob, $whisperJob, $listenerJob -ErrorAction SilentlyContinue
    foreach ($svc in @("ollama","qdrant","node","python")) {
        Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
