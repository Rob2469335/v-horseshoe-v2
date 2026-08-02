@echo off
echo Starting llama.cpp Server with Vulkan acceleration and KV cache quantization...
echo.
echo Model: Qwen3.5-9B-Q4_K_M
echo Context: 16384 tokens
echo Memory Optimizations: Flash Attention (-fa on), 8-bit KV Cache (-ctk q8_0 -ctv q8_0), CPU P-cores (-t 2 -ngl 0)
echo.

rem Speculative decoding (off by default): set SWARM_SPEC_DECODE=1 and optionally
rem SWARM_SPEC_TYPE (default ngram-simple; use "draft-mtp,ngram-simple" with an MTP
rem GGUF, or "draft-simple,..." with SWARM_DRAFT_MODEL for a same-vocab 0.8B draft).
set "SPEC_ARGS="
set "SPEC_TYPE=%SWARM_SPEC_TYPE%"
if not defined SPEC_TYPE set "SPEC_TYPE=ngram-simple"
set "NG_ARGS="
echo %SPEC_TYPE%|findstr /i "ngram" >nul && set "NG_ARGS= --spec-ngram-simple-size-n 4 --spec-ngram-simple-size-m 16 --spec-ngram-simple-min-hits 1"
if "%SWARM_SPEC_DECODE%"=="1" set "SPEC_ARGS=--spec-type %SPEC_TYPE%%NG_ARGS%"
if defined SWARM_DRAFT_MODEL set "SPEC_ARGS=%SPEC_ARGS% --model-draft %SWARM_DRAFT_MODEL%"

rem Local generation model override (off by default): set SWARM_LOCAL_MODEL:
rem   qwen3.5-4b     = plain 4B Q4_K_M on the iGPU (-ngl 99), ~6.4 t/s
rem   qwen3.5-4b-mtp = unsloth MTP 4B (UD-Q4_K_XL) for --spec-type draft-mtp,
rem                    measured ~13 t/s (~2x) with draft-mtp+ngram on this iGPU.
rem The qwen3.5-9b alias is kept so the runtime is unchanged.
set "GEN_MODEL=C:\Users\rober\Projects\v-horseshoe-v2\models\Qwen3.5-9B-Q4_K_M.gguf"
set "GEN_ALIAS=qwen3.5-9b"
set "GEN_NGL=0"
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

bin\llama.exe serve -m "%GEN_MODEL%" --alias "%GEN_ALIAS%" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --cache-reuse 256 -ngl %GEN_NGL% --port 8080 %SPEC_ARGS%

pause
