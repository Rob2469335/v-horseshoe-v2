@echo off
echo Starting llama.cpp Server with Vulkan acceleration and KV cache quantization...
echo.
echo Model: Qwen3.5-4B-UD-Q4_K_XL (MTP) on the iGPU (-ngl 99)
echo Context: 16384 tokens
echo Memory Optimizations: Flash Attention (-fa on), 8-bit KV Cache (-ctk q8_0 -ctv q8_0), iGPU via Vulkan (-ngl 99)
echo.

rem Speculative decoding (default ON - ngram-mod is free, no extra model): set
rem SWARM_SPEC_DECODE=0 to disable. SWARM_SPEC_TYPE selects the implementation
rem (default ngram-mod; "draft-mtp,ngram-simple" for the MTP head, or
rem "draft-simple,..." with SWARM_DRAFT_MODEL for a same-vocab 0.8B draft).
rem ngram-mod measured 21.4 t/s on the tool-decision workload (~3.6x plain, best
rem of all spec types - see AGENTS.md). Note: draft-model spec-decode is
rem structurally broken on UMA iGPUs (two models serialize on one Vulkan queue,
rem llama.cpp#23126), so ngram-mod is the right choice here. ngram-simple is the
rem fallback.
set "SPEC_ARGS="
set "SPEC_TYPE=%SWARM_SPEC_TYPE%"
if not defined SPEC_TYPE set "SPEC_TYPE=ngram-mod"
set "NG_ARGS="
echo %SPEC_TYPE%|findstr /i "ngram-mod" >nul && set "NG_ARGS= --spec-ngram-mod-n-match 24 --spec-ngram-mod-n-min 48 --spec-ngram-mod-n-max 64"
echo %SPEC_TYPE%|findstr /i "ngram-simple" >nul && set "NG_ARGS= --spec-ngram-simple-size-n 4 --spec-ngram-simple-size-m 16 --spec-ngram-simple-min-hits 1"
set "SWARM_SPEC_DECODE=%SWARM_SPEC_DECODE%"
if not defined SWARM_SPEC_DECODE set "SWARM_SPEC_DECODE=1"
if "%SWARM_SPEC_DECODE%"=="1" set "SPEC_ARGS=--spec-type %SPEC_TYPE%%NG_ARGS%"
if defined SWARM_DRAFT_MODEL set "SPEC_ARGS=%SPEC_ARGS% --model-draft %SWARM_DRAFT_MODEL%"

rem Runtime speed gates (default ON): grammar-constrained local tool decisions
rem (valid JSON first try, fewer retries) and the semantic decision cache
rem (near-duplicate decisions short-circuit the LLM). Both never block on errors.
set "SWARM_GRAMMAR_DECODE=%SWARM_GRAMMAR_DECODE%"
if not defined SWARM_GRAMMAR_DECODE set "SWARM_GRAMMAR_DECODE=1"
set "SWARM_SEMANTIC_CACHE=%SWARM_SEMANTIC_CACHE%"
if not defined SWARM_SEMANTIC_CACHE set "SWARM_SEMANTIC_CACHE=1"

rem Local generation model (DEFAULT): the unsloth MTP 4B (UD-Q4_K_XL) on the iGPU
rem (-ngl 99) - ~21 t/s (tool-decision) with SWARM_SPEC_DECODE=1 (ngram-mod default),
rem or ~6.4 t/s plain. The qwen3.5-4b,qwen3.5-9b alias keeps the runtime unchanged.
rem Override with SWARM_LOCAL_MODEL:
rem   qwen3.5-9b     = plain 9B Q4_K_M, CPU-native (-ngl 0), ~5.5-6.0 t/s - quality
rem                    fallback for cloud-offline periods (9B MTP is a dud, +21%)
rem   qwen3.5-4b     = plain 4B Q4_K_M on the iGPU (backward compat)
rem   qwen3.5-4b-mtp = MTP 4B (same as default; backward compat)
set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
set "GEN_ALIAS=qwen3.5-4b,qwen3.5-9b"
set "GEN_NGL=99"
if "%SWARM_LOCAL_MODEL%"=="qwen3.5-9b" (
    set "GEN_MODEL=C:\Users\rober\Projects\v-horseshoe-v2\models\Qwen3.5-9B-Q4_K_M.gguf"
    set "GEN_ALIAS=qwen3.5-9b"
    set "GEN_NGL=0"
)
if "%SWARM_LOCAL_MODEL%"=="qwen3.5-4b" (
    set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf"
    set "GEN_ALIAS=qwen3.5-4b,qwen3.5-9b"
    set "GEN_NGL=99"
)
if "%SWARM_LOCAL_MODEL%"=="qwen3.5-4b-mtp" (
    set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
    set "GEN_ALIAS=qwen3.5-4b,qwen3.5-9b"
    set "GEN_NGL=99"
)

bin\llama.exe serve -m "%GEN_MODEL%" --alias "%GEN_ALIAS%" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --cache-reuse 1024 -ngl %GEN_NGL% --port 8080 %SPEC_ARGS%

pause
