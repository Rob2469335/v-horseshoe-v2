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

# Non-secret environment defaults. Cloud API keys are intentionally NOT hardcoded
# here — they belong in `.env` (gitignored), which is loaded above. A missing key
# disables the associated cloud service and the runtime falls back to local models.
if (-not $env:LLAMA_BASE_URL)       { $env:LLAMA_BASE_URL       = "http://localhost:8080" }
if (-not $env:OLLAMA_BASE_URL)      { $env:OLLAMA_BASE_URL      = "http://localhost:8080" }
if (-not $env:QDRANT_LOCAL)         { $env:QDRANT_LOCAL         = "true" }
if (-not $env:OPENAI_API_BASE)      { $env:OPENAI_API_BASE      = "https://opencode.ai/zen/go/v1" }

if (-not $env:VH2_QDRANT_ENABLED)   { $env:VH2_QDRANT_ENABLED   = "true" }
if (-not $env:VH2_RERANKER_ENABLED) { $env:VH2_RERANKER_ENABLED = "true" }
if (-not $env:ZENITH_WEATHER_CITY)  { $env:ZENITH_WEATHER_CITY  = "New York" }

# Warn when a cloud API key is missing so degraded startup is visible, not silent.
$missingKeys = @()
foreach ($k in @("OPENROUTER_API_KEY","NVIDIA_API_KEY","GEMINI_API_KEY","GROQ_API_KEY","OPENAI_API_KEY","API_KEY","TAVILY_API_KEY","SERPER_API_KEY","BRAVE_API_KEY","EXA_API_KEY","SERPAPI_KEY","TINYFISH_API_KEY")) {
    if (-not [Environment]::GetEnvironmentVariable($k)) { $missingKeys += $k }
}
if ($missingKeys.Count -gt 0) {
    Write-Host "Missing cloud API keys (services will use local fallbacks): $($missingKeys -join ', ')" -ForegroundColor Yellow
    Write-Host "  Add them to .env to enable cloud models/features." -ForegroundColor Yellow
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
Write-Host "DeepSeek key present:     $([bool]$env:DEEPSEEK_API_KEY)" -ForegroundColor Gray
Write-Host "Tavily key present:       $([bool]$env:TAVILY_API_KEY)" -ForegroundColor Gray
Write-Host "Brave key present:        $([bool]$env:BRAVE_API_KEY)" -ForegroundColor Gray
Write-Host "Exa key present:          $([bool]$env:EXA_API_KEY)" -ForegroundColor Gray
Write-Host "Serper key present:       $([bool]$env:SERPER_API_KEY)" -ForegroundColor Gray
Write-Host "SerpApi key present:      $([bool]$env:SERPAPI_KEY)" -ForegroundColor Gray
Write-Host "TinyFish key present:     $([bool]$env:TINYFISH_API_KEY)" -ForegroundColor Gray
Write-Host "Firecrawl key present:    $([bool]$env:FIRECRAWL_API_KEY)" -ForegroundColor Gray
Write-Host "Scavio key present:       $([bool]$env:SCAVIO_API_KEY)" -ForegroundColor Gray
Write-Host "OpenAI/OpenCode key:      $([bool]$env:OPENAI_API_KEY)" -ForegroundColor Gray
Write-Host "Generic API key:          $([bool]$env:API_KEY)" -ForegroundColor Gray
Write-Host "OpenCode Base URL:        $([bool]$env:OPENAI_API_BASE)" -ForegroundColor Gray

# STEP 1 - Cleanup
Write-Host "`n[STEP 1] Cleaning up..." -ForegroundColor Yellow
foreach ($svc in @("llama-server","qdrant","node","python")) {
    Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "Cleanup complete ✔" -ForegroundColor Green

# STEP 2 - llama.cpp microservices
Write-Host "`n[STEP 2] Starting llama.cpp Microservices (Ports 8080-8084)..." -ForegroundColor Yellow

# Runtime speed gates (default ON): grammar-constrained local tool decisions
# (valid JSON first try, fewer retries) + semantic decision cache (near-duplicate
# decisions short-circuit the LLM). Both never block on errors. Set env to "0" to
# disable.
if (-not $env:SWARM_GRAMMAR_DECODE) { $env:SWARM_GRAMMAR_DECODE = "1" }
if (-not $env:SWARM_SEMANTIC_CACHE)  { $env:SWARM_SEMANTIC_CACHE = "1" }

# Speculative decoding (default ON - ngram-mod is free, no extra model): disable
# with $env:SWARM_SPEC_DECODE = "0". NOTE: draft-model spec-decode is structurally
# broken on UMA iGPUs (two models serialize on one Vulkan queue, llama.cpp#23126),
# so ngram-mod is the correct choice here - the 0.8B draft is a 2.4x regression.
# $env:SWARM_SPEC_TYPE selects the implementation (default "ngram-mod"):
#   - "ngram-mod"               : modified-ngram drafts, no extra model. Measured
#     21.4 t/s on the tool-decision workload (~3.6x plain, best of all types - see
#     AGENTS.md). Uses a stable system prompt/tool schema to draft long continuations.
#   - "ngram-simple"            : pattern-matching drafts, no extra model (fallback)
#   - "draft-mtp,ngram-simple"  : Qwen3.5 built-in MTP head + ngram. REQUIRES an MTP
#     GGUF (e.g. unsloth Qwen3.5-4B-MTP; the plain Qwen3.5-4B GGUF has NO MTP head).
#     UD-Q4_K_XL 4B measured 66% acceptance, ~2.0x on the iGPU.
#   - "draft-simple,ngram-simple" + $env:SWARM_DRAFT_MODEL : in-process 0.8B draft
#     (Qwen3.5-0.8B shares the family vocab; verified identical tokenization).
$specArgs = @()
if ($env:SWARM_SPEC_DECODE -ne "0") {
    $specType = if ($env:SWARM_SPEC_TYPE) { $env:SWARM_SPEC_TYPE } else { "ngram-mod" }
    $specArgs = @("--spec-type", $specType)
    # n-match=16/n-min=32/n-max=64: balanced for tool-decision JSON (~50-100 tokens).
    # The benchmarked 21 t/s used n-match=24 on long prose; on short structured outputs
    # that setting yielded only 15.6% acceptance. These values match llama.cpp tool-call
    # benchmark guidance and should recover acceptance to >40%.
    if ($specType -like "*ngram-mod*")    { $specArgs += @("--spec-ngram-mod-n-match", "16", "--spec-ngram-mod-n-min", "32", "--spec-ngram-mod-n-max", "64") }
    if ($specType -like "*ngram-simple*") { $specArgs += @("--spec-ngram-simple-size-n", "4", "--spec-ngram-simple-size-m", "16", "--spec-ngram-simple-min-hits", "1") }
    if ($specType -like "*draft-mtp*")     { $specArgs += @("--spec-draft-n-max", "3") }
    if ($env:SWARM_DRAFT_MODEL)            { $specArgs += @("--model-draft", $env:SWARM_DRAFT_MODEL) }
}

# Local generation model (DEFAULT): the unsloth MTP 4B (UD-Q4_K_XL) on the iGPU
# (-ngl 99) - ~21 t/s (tool-decision) with SWARM_SPEC_DECODE=1 (ngram-mod default),
# or ~6.4 t/s plain. Served under the honest alias "qwen3.5-4b".
# Override with $env:SWARM_LOCAL_MODEL:
#   - "qwen3.5-4b"     : plain 4B Q4_K_M on the iGPU (same alias)
#   - "qwen3.5-4b-mtp" : MTP 4B (same as default)
$genModel = "C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
$genAlias = "qwen3.5-4b"
$genNgl = "99"
if ($env:SWARM_LOCAL_MODEL -eq "qwen3.5-4b") {
    $genModel = "C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf"
    $genAlias = "qwen3.5-4b"
    $genNgl = "99"
}
if ($env:SWARM_LOCAL_MODEL -eq "qwen3.5-4b-mtp") {
    $genModel = "C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
    $genAlias = "qwen3.5-4b"
    $genNgl = "99"
}
# BUG FIX: cache-reuse is not supported by MTP GGUFs (kv_unified=false).
# Passing it generates a warning on every boot and the flag is silently dropped.
# Only set it for non-MTP models (plain Q4_K_M builds don't contain 'UD' in name).
$cacheReuseArg = if ($genModel -like "*UD*") { @() } else { @("--cache-reuse", "1024") }
# BUG FIX: $specArgs was built above but never passed into Start-Job — the previous
# code hardcoded --spec-type and ngram params inline, so SWARM_SPEC_DECODE=0 had
# no effect, draft-mtp missed --spec-draft-n-max 3, and SWARM_DRAFT_MODEL was
# silently ignored. Now passes $specArgs + $cacheReuseArg as ArgumentList and uses
# @spec / @cacheReuse splatting inside the ScriptBlock.
$llamaGenJob = Start-Job -ScriptBlock { param($r, $spec, $cacheReuse, $m, $a, $ngl); Set-Location $r; & .\bin\llama.exe serve -m $m --alias $a -c 16384 -ctk q8_0 -ctv q8_0 -fa on -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 @cacheReuse --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" -ngl $ngl --port 8080 @spec 2>&1 } -ArgumentList $root, $specArgs, $cacheReuseArg, $genModel, $genAlias, $genNgl
$llamaEmbJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m "C:\Users\rober\models\gte-modernbert-base-Q8_0.gguf" --embedding --pooling cls -b 8192 -ub 8192 --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8081 -t 2 2>&1 } -ArgumentList $root
$llamaRerankJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m "C:\Users\rober\models\gte-reranker-modernbert-base-Q8_0.gguf" --reranking --pooling rank -b 8192 -ub 8192 --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8082 -t 2 2>&1 } -ArgumentList $root
$llamaVisJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m "C:\Users\rober\models\Qwen3VL-2B-Instruct-Q4_K_M.gguf" --mmproj "C:\Users\rober\models\mmproj-Qwen3VL-2B-Instruct-F16.gguf" --image-min-tokens 1024 -c 16384 --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8083 -t 2 2>&1 } -ArgumentList $root
# 0.8B summarization server (dedicated to memory consolidation/distiller — fast, lightweight, keeps the main 4B slot free)
$llamaSummJob = Start-Job -ScriptBlock { param($r); Set-Location $r; & .\bin\llama.exe serve -m "C:\Users\rober\models\Qwen3.5-0.8B.Q4_K_M.gguf" --alias "qwen3.5-0.8b" -c 8192 -t 1 -tb 2 -b 512 -ub 256 -np 1 --timeout 120 --api-key "llama" --cors-origins "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000" --port 8084 2>&1 } -ArgumentList $root

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
# SECURITY (2026-08-17 audit): Qdrant's default `service.host` is 0.0.0.0 — it
# listens on every interface with NO auth, exposing the whole memory/chess/legal
# store to the LAN (verified reachable from 10.2.0.2). The env var is the
# documented override (highest priority, cannot move the ./storage data path).
# Bind loopback only: local services reach it at 127.0.0.1; nothing off-box can.
$env:QDRANT__SERVICE__HOST = "127.0.0.1"
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
$mcpRegistered = $false
$tools = $null
for ($i = 0; $i -lt 90; $i++) {
    try {
        $tools = Invoke-RestMethod "http://127.0.0.1:8000/tools"
        # Wait until the MCP servers have actually registered (their tools appear
        # in /tools with the mcp: prefix). The backend loads them in the BACKGROUND
        # after boot, so the first poll may only show the ~22 built-in tools.
        if ($tools.count -gt 0 -and ($tools.capabilities -match "^mcp:") -and $tools.count -gt 22) {
            $mcpRegistered = $true
            break
        }
        Start-Sleep 1
    } catch {
        Start-Sleep 1
    }
}
if ($mcpRegistered) {
    $mcpCount = @($tools.capabilities | Where-Object { $_ -match "^mcp:" }).Count
    Write-Host "MCP Servers ✔  Registered $($tools.count) tools ($mcpCount external MCP)" -ForegroundColor Green
} else {
    Write-Host "MCP Servers ⚠  Backend up; external MCP tools not yet registered (built-in: $($tools.count) tools). Check backend logs." -ForegroundColor Yellow
}

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
Write-Host "llama.cpp: http://127.0.0.1:8080" -ForegroundColor Gray
Write-Host "Frontend:  http://127.0.0.1:5173" -ForegroundColor Gray
Write-Host ""

try {
    while ($true) {
        Receive-Job $llamaGenJob   | ForEach-Object { Write-Host "[llama-gen]    $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaEmbJob   | ForEach-Object { Write-Host "[llama-emb]    $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaRerankJob | ForEach-Object { Write-Host "[llama-rank]   $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaVisJob   | ForEach-Object { Write-Host "[llama-vis]    $_" -ForegroundColor DarkGreen }
        Receive-Job $llamaSummJob  | ForEach-Object { Write-Host "[llama-summ]   $_" -ForegroundColor DarkGreen }
        Receive-Job $backendJob  | ForEach-Object { Write-Host "[backend]  $_" -ForegroundColor DarkCyan }
        Receive-Job $frontendJob | ForEach-Object { Write-Host "[frontend] $_" -ForegroundColor DarkYellow }
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Host "`nShutting down..." -ForegroundColor Red
    Stop-Job  $llamaGenJob, $llamaEmbJob, $llamaRerankJob, $llamaVisJob, $llamaSummJob, $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $llamaGenJob, $llamaEmbJob, $llamaRerankJob, $llamaVisJob, $llamaSummJob, $backendJob, $frontendJob -ErrorAction SilentlyContinue
    foreach ($svc in @("llama","qdrant","node","python")) {
        Get-Process -Name $svc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
