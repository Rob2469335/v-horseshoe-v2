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
rem or ~6.4 t/s plain. Served under the honest alias "qwen3.5-4b".
rem Override with SWARM_LOCAL_MODEL:
rem   qwen3.5-4b     = plain 4B Q4_K_M on the iGPU (same alias)
rem   qwen3.5-4b-mtp = MTP 4B (same as default)
set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
set "GEN_ALIAS=qwen3.5-4b"
set "GEN_NGL=99"
rem NOTE: qwen3.5-4b override disabled — Qwen3.5-4B-Q4_K_M.gguf (plain, non-MTP) was never downloaded.
rem if "%SWARM_LOCAL_MODEL%"=="qwen3.5-4b" (
rem     set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf"
rem     set "GEN_ALIAS=qwen3.5-4b"
rem     set "GEN_NGL=99"
rem )
if "%SWARM_LOCAL_MODEL%"=="qwen3.5-4b-mtp" (
    set "GEN_MODEL=C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf"
    set "GEN_ALIAS=qwen3.5-4b"
    set "GEN_NGL=99"
)
if "%SWARM_LOCAL_MODEL%"=="qwen3.8-4b" (
    set "GEN_MODEL=C:\Users\rober\models\Qwen3.8-4B-Q4_K_M.gguf"
    set "GEN_ALIAS=qwen3.8-4b"
    set "GEN_NGL=99"
)
rem BUG FIX: --cache-reuse 1024 is not supported by MTP GGUFs (kv_unified=false).
rem The flag is silently dropped but generates a warning on every startup.
rem Only set it for non-MTP models (plain Q4_K_M; MTP models have 'UD' in filename).
set "CACHE_REUSE_ARG=--cache-reuse 1024"
echo %GEN_MODEL% | findstr /i "UD" >nul && set "CACHE_REUSE_ARG="

set "LLAMA_PORT_ARG=%LLAMA_PORT%"
if not defined LLAMA_PORT_ARG set "LLAMA_PORT_ARG=8080"

bin\llama.exe serve -m "%GEN_MODEL%" --alias "%GEN_ALIAS%" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 %CACHE_REUSE_ARG% -ngl %GEN_NGL% --port %LLAMA_PORT_ARG% %SPEC_ARGS%
