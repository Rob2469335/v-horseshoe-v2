@echo off
echo Starting llama.cpp Server with Vulkan acceleration and KV cache quantization...
echo.
echo Model: Qwen3.5-9B-Q4_K_M
echo Context: 16384 tokens
echo Memory Optimizations: Flash Attention (-fa on), 8-bit KV Cache (-ctk q8_0 -ctv q8_0), CPU P-cores (-t 2 -ngl 0)
echo.

bin\llama.exe serve -m "C:\Users\rober\Projects\v-horseshoe-v2\models\Qwen3.5-9B-Q4_K_M.gguf" --alias "qwen3.5-9b" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --port 8080

pause
