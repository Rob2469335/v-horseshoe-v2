"""Minimal OpenAI-compatible chat completions server for eval + trace-gen.

If ADAPTER_PATH is set, loads the LoRA adapter on top of the base model.
If ADAPTER_PATH is not set, serves the base model only (for base-control eval).
"""

import os, torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import uvicorn

app = FastAPI()
BASE_MODEL = "Qwen/Qwen3.5-4B"
ADAPTER_PATH = os.environ.get("ADAPTER_PATH", "")
MAX_LEN = int(os.environ.get("MAX_LEN", "4096"))

print("Loading base model...")
tokenizer = AutoTokenizer.from_pretrained(
    BASE_MODEL, trust_remote_code=True, cache_dir="/workspace/hf_cache"
)
model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    torch_dtype=torch.float16,
    device_map="cuda",
    cache_dir="/workspace/hf_cache",
)

if ADAPTER_PATH:
    print(f"Loading adapter from {ADAPTER_PATH}...")
    model = PeftModel.from_pretrained(model, ADAPTER_PATH)
    print("Adapter loaded on top of base model")
else:
    print("No adapter — serving base model only")

print(f"Ready on CUDA: {next(model.parameters()).device}")


class ChatRequest(BaseModel):
    model: str = "qwen3.5-4b"
    messages: list
    max_tokens: int = 4096
    temperature: float = 0.0


@app.get("/v1/models")
def models():
    aid = "v6-lora" if ADAPTER_PATH else "base"
    return {"data": [{"id": aid, "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    text = tokenizer.apply_chat_template(
        req.messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            temperature=req.temperature if req.temperature > 0 else None,
            do_sample=req.temperature > 0,
        )
    gen = out[0][inputs["input_ids"].shape[1] :]
    content = tokenizer.decode(gen, skip_special_tokens=True)
    tok = gen.shape[0]
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "completion_tokens": tok,
            "prompt_tokens": inputs["input_ids"].shape[1],
        },
        "model": req.model,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8086)
