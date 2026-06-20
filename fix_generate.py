import pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\api\routes.py')
src = p.read_text(encoding='utf-8')

old = '@router.post("/generate", response_model=GenerateResponse)\nasync def generate(payload: GenerateRequest, orch: Orchestrator = Depends(get_orchestrator)):\n    try:\n        result, chosen_model = await orch.generate(model=payload.model, prompt=payload.prompt)\n        return GenerateResponse(content=result, model=chosen_model)\n    except Exception as exc:\n        raise HTTPException(status_code=502, detail=str(exc))'

new = '@router.post("/generate")\nasync def generate(payload: GenerateRequest, runtime: Any = Depends(runtime_dep)):\n    model = payload.model or "qwen2.5:7b-instruct"\n    try:\n        async with httpx.AsyncClient(timeout=120.0) as client:\n            r = await client.post(\n                "http://127.0.0.1:11434/api/chat",\n                json={"model": model, "messages": [{"role": "user", "content": payload.prompt}], "stream": False}\n            )\n            r.raise_for_status()\n            data = r.json()\n            content = data.get("message", {}).get("content", "")\n            return {"content": content, "model": model, "choices": [{"message": {"content": content, "tool_calls": []}, "finish_reason": "stop"}], "usage": {"total_tokens": data.get("eval_count", 0), "prompt_tokens": data.get("prompt_eval_count", 0)}}\n    except Exception as exc:\n        raise HTTPException(status_code=502, detail=str(exc))'

if old in src:
    src = src.replace(old, new)
    p.write_text(src, encoding='utf-8')
    print("OK")
else:
    print("NOT FOUND")
