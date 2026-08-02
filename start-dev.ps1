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

# Explicit API key fallback defaults (from ZENITH Swarm OS - API Keys)
if (-not $env:LLAMA_BASE_URL)       { $env:LLAMA_BASE_URL       = "http://localhost:8080" }
if (-not $env:OLLAMA_BASE_URL)      { $env:OLLAMA_BASE_URL      = "http://localhost:8080" }
if (-not $env:QDRANT_LOCAL)         { $env:QDRANT_LOCAL         = "true" }

if (-not $env:TAVILY_API_KEY)       { $env:TAVILY_API_KEY       = "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" }
if (-not $env:SERPER_API_KEY)       { $env:SERPER_API_KEY       = "5751e133a650826ed6f103b2c5a6486a276cdca4" }
if (-not $env:EXA_API_KEY)          { $env:EXA_API_KEY          = "bf1234c7-df6c-4974-aa94-20fd21880de0" }
if (-not $env:SERPAPI_KEY)          { $env:SERPAPI_KEY          = "03307437e7a3ae398613914cf687200db5d66b58ebe863fd136f19d3db325afd" }
if (-not $env:TINYFISH_API_KEY)     { $env:TINYFISH_API_KEY     = "sk-tinyfish-kaspa7FeVMLfRHcsD6HkIAjl-nXj3OVo" }

if (-not $env:GEMINI_API_KEY)       { $env:GEMINI_API_KEY       = "AQ.Ab8RN6JIZuDjNviEEhROdpzwkpDWKGIbnrBTjqC7wTtcD-stLw" }
if (-not $env:BRAVE_API_KEY)        { $env:BRAVE_API_KEY        = "BSAwBqBtiiEbP8GvFvNPF66dc_eslQa" }
if (-not $env:GROQ_API_KEY)         { $env:GROQ_API_KEY         = "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" }
if (-not $env:OPENROUTER_API_KEY)   { $env:OPENROUTER_API_KEY   = "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" }
if (-not $env:NVIDIA_API_KEY)       { $env:NVIDIA_API_KEY       = "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" }
if (-not $env:API_KEY)              { $env:API_KEY              = "pk-prov-7MFTHz2RyPZnabRoRwGF3V2vzq6xCzR3rwf35fkS3KMK" }
if (-not $env:OPENAI_API_KEY)       { $env:OPENAI_API_KEY       = "sk-XmqUiHu0HlnSravUHSj32BGyzqpKeMlmMNeOQPk5FHmg3cazfwGvCroLKa1XSVlx" }
if (-not $env:OPENAI_API_BASE)      { $env:OPENAI_API_BASE      = "https://api.opencode.go/v1" }

if (-not $env:VH2_QDRANT_ENABLED)   { $env:VH2_QDRANT_ENABLED   = "true" }
if (-not $env:VH2_RERANKER_ENABLED) { $env:VH2_RERANKER_ENABLED = "true" }
if (-not $env:ZENITH_WEATHER_CITY)  { $env:ZENITH_WEATHER_CITY  = "New York" }

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
Write-Host "OpenAI/OpenCode key:      $([bool]$env:OPENAI_API_KEY)" -ForegroundColor Gray
Write-Host "OpenCode Base URL:        $([bool]$env:OPENAI_API_BASE)" -ForegroundColor Gray

# STEP 1 - Cleanup
Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("llama-server","qdrant","node","python")) {
    Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "Cleanup complete ✔" -ForegroundColor Green

# STEP 2 - llama.cpp microservices
Write-Host "`n[STEP 2] Starting llama.cpp Microservices (Ports 8080-8083)..." -ForegroundColor Yellow

# To switch to the 35B model, uncomment the line below and comment out the 9B line.
# $llamaGenJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m ".\models\qwen-tuned-latest.gguf" -c 32768 -ctk q8_0 -ctv q8_0 -t 4 -b 256 -ub 512 --port 8080 2>&1 } -ArgumentList $root
# Speculative decoding (off by default): enable with $env:SWARM_SPEC_DECODE = "1".
# $env:SWARM_SPEC_TYPE selects the implementation (default "ngram-simple"):
#   - "ngram-simple"            : pattern-matching drafts, no extra model (measured ~2x)
#   - "draft-mtp,ngram-simple"  : Qwen3.5 built-in MTP head + ngram. REQUIRES an MTP
#     GGUF (e.g. unsloth Qwen3.5-4B-MTP / -9B-MTP; the plain Qwen3.5-9B/4B GGUFs have
#     NO MTP head). UD-Q4_K_XL 4B measured 66% acceptance, ~2.0x on the iGPU.
#   - "draft-simple,ngram-simple" + $env:SWARM_DRAFT_MODEL : in-process 0.8B draft
#     (Qwen3.5-0.8B shares the family vocab; verified identical tokenization).
$specArgs = @()
if ($env:SWARM_SPEC_DECODE -eq "1") {
    $specType = if ($env:SWARM_SPEC_TYPE) { $env:SWARM_SPEC_TYPE } else { "ngram-simple" }
    $specArgs = @("--spec-type", $specType)
    if ($specType -like "*ngram-simple*") { $specArgs += @("--spec-ngram-simple-size-n", "4", "--spec-ngram-simple-size-m", "16", "--spec-ngram-simple-min-hits", "1") }
    if ($specType -like "*draft-mtp*")     { $specArgs += @("--spec-draft-n-max", "3") }
    if ($env:SWARM_DRAFT_MODEL)            { $specArgs += @("--model-draft", $env:SWARM_DRAFT_MODEL) }
}

# Local generation model (DEFAULT): the unsloth MTP 4B (UD-Q4_K_XL) on the iGPU
# (-ngl 99) - ~13 t/s with SWARM_SPEC_DECODE=1 + SWARM_SPEC_TYPE=draft-mtp,ngram-simple,
# or ~6.4 t/s plain. The qwen3.5-4b,qwen3.5-9b alias keeps the runtime unchanged.
# Override with $env:SWARM_LOCAL_MODEL:
#   - "qwen3.5-9b"     : plain 9B Q4_K_M, CPU-native (-ngl 0), ~5.5-6.0 t/s - quality
#                        fallback for cloud-offline periods (9B MTP is a dud: +21%,
#                        50% acceptance - see AGENTS.md)
#   - "qwen3.5-4b"     : plain 4B Q4_K_M on the iGPU (backward compat)
#   - "qwen3.5-4b-mtp" : MTP 4B (same as default; backward compat)
$genModel = "C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
$genAlias = "qwen3.5-4b,qwen3.5-9b"
$genNgl = "99"
if ($env:SWARM_LOCAL_MODEL -eq "qwen3.5-9b") {
    $genModel = ".\models\Qwen3.5-9B-Q4_K_M.gguf"
    $genAlias = "qwen3.5-9b"
    $genNgl = "0"
}
if ($env:SWARM_LOCAL_MODEL -eq "qwen3.5-4b") {
    $genModel = "C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf"
    $genAlias = "qwen3.5-4b,qwen3.5-9b"
    $genNgl = "99"
}
if ($env:SWARM_LOCAL_MODEL -eq "qwen3.5-4b-mtp") {
    $genModel = "C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
    $genAlias = "qwen3.5-4b,qwen3.5-9b"
    $genNgl = "99"
}
$llamaGenJob = Start-Job -ScriptBlock { param($r, $spec, $m, $a, $ngl); Set-Location $r; & .\bin\llama.exe serve -m $m --alias $a -c 16384 -ctk q8_0 -ctv q8_0 -fa on -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --cache-reuse 256 --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" -ngl $ngl --port 8080 @spec 2>&1 } -ArgumentList $root, $specArgs, $genModel, $genAlias, $genNgl
$llamaEmbJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m ".\models\nomic-embed-text-v1.5.Q8_0.gguf" --embedding --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8081 -t 2 2>&1 } -ArgumentList $root
$llamaRerankJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m ".\models\qllama-bge-reranker-v2-m3-latest.gguf" --reranking --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8082 -t 2 2>&1 } -ArgumentList $root
$llamaVisJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m ".\models\moondream-latest.gguf" --override-kv "tokenizer.ggml.pre=str:default" --chat-template "vicuna" --mmproj-auto --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8083 -t 2 2>&1 } -ArgumentList $root

Start-Sleep -Seconds 8
Write-Host "llama.cpp microservices ✔" -ForegroundColor Green

# STEP 3 - Qdrant (wait until actually ready)
Write-Host "`n[STEP 3] Starting Qdrant..." -ForegroundColor Yellow
$qdrantPath = "$root\qdrant-bin\qdrant.exe"
if (-not (Test-Path $qdrantPath)) {
    $qdrantPath = "C:\Users\rober\Documents\v-horseshoe-sync\qdrant-bin\qdrant.exe"
}
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
Write-Host "llama.cpp: http://127.0.0.1:8080" -ForegroundColor Gray
Write-Host "Frontend:  http://127.0.0.1:5173" -ForegroundColor Gray
Write-Host "Whisper:   http://127.0.0.1:8001" -ForegroundColor Gray
Write-Host "Listener:  Active" -ForegroundColor Gray
Write-Host ""

try {
    while ($true) {
        Receive-Job $llamaGenJob   | ForEach-Object { Write-Host "[llama-gen]    $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaEmbJob   | ForEach-Object { Write-Host "[llama-emb]    $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaRerankJob | ForEach-Object { Write-Host "[llama-rank]   $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaVisJob   | ForEach-Object { Write-Host "[llama-vis]    $_" -ForegroundColor DarkGreen }
        Receive-Job $backendJob  | ForEach-Object { Write-Host "[backend]  $_" -ForegroundColor DarkCyan }
        Receive-Job $frontendJob | ForEach-Object { Write-Host "[frontend] $_" -ForegroundColor DarkYellow }
        Receive-Job $whisperJob  | ForEach-Object { Write-Host "[whisper]  $_" -ForegroundColor DarkMagenta }
        Receive-Job $listenerJob | ForEach-Object { Write-Host "[listener] $_" -ForegroundColor DarkYellow }
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Red
    Stop-Job  $llamaGenJob, $llamaEmbJob, $llamaRerankJob, $llamaVisJob, $backendJob, $frontendJob, $whisperJob, $listenerJob -ErrorAction SilentlyContinue
    Remove-Job $llamaGenJob, $llamaEmbJob, $llamaRerankJob, $llamaVisJob, $backendJob, $frontendJob, $whisperJob, $listenerJob -ErrorAction SilentlyContinue
    foreach ($svc in @("llama","qdrant","node","python")) {
        Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
