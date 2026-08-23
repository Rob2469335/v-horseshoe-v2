$ErrorActionPreference = "SilentlyContinue"

Write-Host "Starting daily microservices (4B on 8079, plus 8081, 8082, 8083, 8084)..." -ForegroundColor Yellow

$root = "C:\Users\rober\Projects\v-horseshoe-v2"
Set-Location $root

$env:SWARM_LOCAL_MODEL = "qwen3.8-4b"
$env:LLAMA_PORT = "8079"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c .\start_llama.bat" -WindowStyle Hidden -WorkingDirectory $root

Write-Host "Starting daily microservices (8081, 8082, 8084)..." -ForegroundColor Yellow

# 8081: Embeddings
Start-Process -FilePath ".\bin\llama.exe" -ArgumentList "serve -m `"C:\Users\rober\models\gte-modernbert-base-Q8_0.gguf`" --embedding --pooling cls -b 8192 -ub 8192 --api-key `"llama`" --cors-origins `"http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000`" --port 8081 -t 2" -WindowStyle Hidden -WorkingDirectory $root

# 8082: Reranker
Start-Process -FilePath ".\bin\llama.exe" -ArgumentList "serve -m `"C:\Users\rober\models\gte-reranker-modernbert-base-Q8_0.gguf`" --reranking --pooling rank -b 8192 -ub 8192 --api-key `"llama`" --cors-origins `"http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000`" --port 8082 -t 2" -WindowStyle Hidden -WorkingDirectory $root

# 8083: Vision Model Router (Qwen3-VL 2B + GLM-OCR)
Start-Process -FilePath ".\bin\llama.exe" -ArgumentList "serve --models-preset `"config\vision_models.ini`" --image-min-tokens 1024 --api-key `"llama`" --cors-origins `"http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000`" --port 8083 -t 2" -WindowStyle Hidden -WorkingDirectory $root

# 8084: Summarizer
Start-Process -FilePath ".\bin\llama.exe" -ArgumentList "serve -m `"C:\Users\rober\models\Qwen3.5-0.8B.Q4_K_M.gguf`" --alias `"qwen3.5-0.8b`" -c 8192 -t 1 -tb 2 -b 512 -ub 256 -np 1 --timeout 120 --api-key `"llama`" --cors-origins `"http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000`" --port 8084" -WindowStyle Hidden -WorkingDirectory $root

Write-Host "Daily microservices launched." -ForegroundColor Green
