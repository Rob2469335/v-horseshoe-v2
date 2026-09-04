"""Minimal OpenAI-compatible chat completions server for trace-gen."""
import torch, time
from fastapi import FastAPI

from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import uvicorn, asyncio

app = FastAPI()
MODEL_PATH = "/workspace/hf_cache/models--Qwen--Qwen3.5-4B/snapshots/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"
print("Loading model...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, torch_dtype=torch.float16, device_map="cuda")
print(f"Model loaded on {next(model.parameters()).device}")

class ChatRequest(BaseModel):
    model: str = "qwen3.5-4b"
    messages: list
    max_tokens: int = 2048
    temperature: float = 0.0

@app.get("/v1/models")
def models():
    return {"data": [{"id": "qwen3.5-4b", "object": "model"}]}

@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    text = tokenizer.apply_chat_template(req.messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=req.max_tokens, temperature=req.temperature if req.temperature > 0 else None, do_sample=req.temperature > 0)
    
    gen = out[0][inputs["input_ids"].shape[1]:]
    content = tokenizer.decode(gen, skip_special_tokens=True)
    tok = gen.shape[0]
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": tok, "prompt_tokens": inputs["input_ids"].shape[1]},
        "model": req.model,
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8086)
