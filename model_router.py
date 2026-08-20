import json
import subprocess
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import uvicorn
import os
import psutil

app = FastAPI()
# Base URL for the underlying llama.cpp server
BACKEND_URL = "http://127.0.0.1:8079"

# Initialize globals to None, to be set in startup_event
client = None
mode_switch_lock = None

current_mode = "daily"
active_processes = []

def _kill_sync():
    print("Killing active llama.cpp processes managed by proxy...")
    # Terminate the cmd wrappers without blocking
    for p in active_processes:
        try:
            p.terminate()
            p.wait(timeout=5)  # Reaping inside the thread is safe
        except Exception:
            pass
    active_processes.clear()
    
    # Ensure no orphaned llama.exe processes are left alive on Windows from THIS project
    for proc in psutil.process_iter(['name', 'cwd']):
        try:
            name = proc.info.get('name')
            cwd = proc.info.get('cwd') or ""
            if name and 'llama' in name.lower() and 'v-horseshoe-v2' in cwd.lower():
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            pass

async def kill_active_processes():
    await asyncio.to_thread(_kill_sync)

async def start_heavy_model():
    print("Starting 14B model on port 8079...")
    env = os.environ.copy()
    env["SWARM_LOCAL_MODEL"] = "qwen3-14b"
    env["LLAMA_PORT"] = "8079"
    p = subprocess.Popen(
        ["cmd.exe", "/c", "start_llama.bat"],
        cwd="C:\\Users\\rober\\Projects\\v-horseshoe-v2",
        env=env
    )
    active_processes.append(p)
    await asyncio.sleep(15)  # give it time to load

async def start_daily_models():
    print("Starting daily models (4B on 8079 + embeddings + reranker + vision + summarizer)...")
    env = os.environ.copy()
    cwd = "C:\\Users\\rober\\Projects\\v-horseshoe-v2"
    
    # 4B Text Model
    env["SWARM_LOCAL_MODEL"] = "qwen3.5-4b-mtp"
    env["LLAMA_PORT"] = "8079"
    p_4b = subprocess.Popen(["cmd.exe", "/c", "start_llama.bat"], cwd=cwd, env=env)
    active_processes.append(p_4b)
    
    # Embeddings
    p_emb = subprocess.Popen(
        [".\\bin\\llama.exe", "serve", "-m", "C:\\Users\\rober\\models\\gte-modernbert-base-Q8_0.gguf", 
         "--embedding", "--pooling", "cls", "-b", "8192", "-ub", "8192", "--api-key", "llama", "--cors-origins", "*", "--port", "8081", "-t", "2"],
        cwd=cwd
    )
    active_processes.append(p_emb)
    
    # Reranker
    p_rerank = subprocess.Popen(
        [".\\bin\\llama.exe", "serve", "-m", "C:\\Users\\rober\\models\\gte-reranker-modernbert-base-Q8_0.gguf", 
         "--reranking", "--pooling", "rank", "-b", "8192", "-ub", "8192", "--api-key", "llama", "--cors-origins", "*", "--port", "8082", "-t", "2"],
        cwd=cwd
    )
    active_processes.append(p_rerank)
    
    # Vision
    p_vis = subprocess.Popen(
        [".\\bin\\llama.exe", "serve", "-m", "C:\\Users\\rober\\models\\Qwen3VL-2B-Instruct-Q4_K_M.gguf", 
         "--mmproj", "C:\\Users\\rober\\models\\mmproj-Qwen3VL-2B-Instruct-F16.gguf", "--image-min-tokens", "1024", "-c", "16384", "--api-key", "llama", "--cors-origins", "*", "--port", "8083", "-t", "2"],
        cwd=cwd
    )
    active_processes.append(p_vis)
    
    # Summarizer
    p_summ = subprocess.Popen(
        [".\\bin\\llama.exe", "serve", "-m", "C:\\Users\\rober\\models\\Qwen3.5-0.8B.Q4_K_M.gguf", 
         "--alias", "qwen3.5-0.8b", "-c", "8192", "-t", "1", "-tb", "2", "-b", "512", "-ub", "256", "-np", "1", "--timeout", "120", "--api-key", "llama", "--cors-origins", "*", "--port", "8084"],
        cwd=cwd
    )
    active_processes.append(p_summ)
    
    await asyncio.sleep(15)

async def switch_mode_if_needed(model_id: str):
    global current_mode
    is_heavy = "14b" in model_id.lower()
    
    async with mode_switch_lock:
        if is_heavy and current_mode != "heavy":
            print(f"Request for {model_id} - Switching to HEAVY mode")
            current_mode = None  # Prevent CancelledError from leaving state out of sync
            try:
                await kill_active_processes()
                await asyncio.sleep(1)
                await start_heavy_model()
                current_mode = "heavy"
            except Exception as e:
                print(f"Failed to start heavy model: {e}")
        elif not is_heavy and current_mode != "daily":
            print(f"Request for {model_id} - Switching to DAILY mode")
            current_mode = None
            try:
                await kill_active_processes()
                await asyncio.sleep(1)
                await start_daily_models()
                current_mode = "daily"
            except Exception as e:
                print(f"Failed to start daily models: {e}")

@app.on_event("startup")
async def startup_event():
    global client, mode_switch_lock
    client = httpx.AsyncClient(timeout=300.0)
    mode_switch_lock = asyncio.Lock()
    # Start the default models immediately on startup
    await start_daily_models()

@app.on_event("shutdown")
async def shutdown_event():
    await kill_active_processes()
    if client:
        await client.aclose()

@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            model = data.get("model") or ""
            await switch_mode_if_needed(str(model))
    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
    except asyncio.CancelledError:
        print("Request cancelled during model swap")
        raise
    except Exception as e:
        print(f"Error checking model swap: {e}")
    
    req = client.build_request("POST", f"{BACKEND_URL}/v1/chat/completions",
                               content=body,
                               headers={k: v for k, v in request.headers.items() if k.lower() not in ['host', 'accept-encoding']})
    try:
        resp = await client.send(req, stream=True)
        # Using a generator to stream back using the global client
        async def stream_generator():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except Exception as e:
                print(f"Stream interrupted: {e}")
            finally:
                await resp.aclose()
        # Strip duplicate transfer-encoding and content-length headers
        headers = dict(resp.headers)
        headers.pop('transfer-encoding', None)
        headers.pop('content-encoding', None)
        headers.pop('content-length', None)
        return StreamingResponse(stream_generator(), status_code=resp.status_code, headers=headers)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

@app.get("/v1/models")
async def proxy_models(request: Request):
    # Spoof the models so OpenCode knows both exist
    spoofed = {
      "object": "list",
      "data": [
        {
          "id": "qwen3.5-4b",
          "object": "model",
          "owned_by": "llamacpp"
        },
        {
          "id": "qwen3-14b-iq4_xs",
          "object": "model",
          "owned_by": "llamacpp"
        }
      ]
    }
    return JSONResponse(spoofed)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
