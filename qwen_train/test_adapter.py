"""Quick adapter smoke test: load base + LoRA, generate text, verify coherence."""
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

ADAPTER_PATH = "C:/Users/rober/Projects/qwen3_5_4b_real25_v4_lora/adapter"
BASE_PATH = "C:/Users/rober/Models/Qwen3.5-4B-Base-HF"

print("Loading tokenizer...")
tok = AutoTokenizer.from_pretrained(ADAPTER_PATH, trust_remote_code=True)

print("Loading base model on CPU...")
base = AutoModelForCausalLM.from_pretrained(
    BASE_PATH,
    torch_dtype=torch.float16,
    trust_remote_code=True,
    device_map="cpu",
)

print("Loading LoRA adapter...")
model = PeftModel.from_pretrained(base, ADAPTER_PATH)

print("Merging adapter...")
model = model.merge_and_unload()
print(f"Merged. Params: {sum(p.numel() for p in model.parameters()) / 1e9:.2f}B")

# --- Test 1: multi-layer reasoning ---
messages = [
    {"role": "system", "content": "You are an expert developer. Think step by step, then provide a concise answer."},
    {"role": "user", "content": "What is the output of this Python code?\n```python\nimport math\nresult = math.sqrt(144) + math.factorial(4)\nprint(result)\n```"},
]
prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = tok(prompt, return_tensors="pt")
print(f"\nTest 1 prompt tokens: {inputs.input_ids.shape[1]}")

with torch.no_grad():
    out = model.generate(
        **inputs,
        max_new_tokens=256,
        temperature=0.7,
        do_sample=True,
        repetition_penalty=1.1,
    )
text = tok.decode(out[0][inputs.input_ids.shape[1] :], skip_special_tokens=True)
print(f"Test 1 output:\n{text.strip()}")

# --- Test 2: structured format ---
messages2 = [
    {"role": "system", "content": "Answer concisely."},
    {"role": "user", "content": "List 3 Python built-in functions that start with the letter 'm'. Format as a numbered list."},
]
prompt2 = tok.apply_chat_template(messages2, tokenize=False, add_generation_prompt=True)
inputs2 = tok(prompt2, return_tensors="pt")
print(f"\nTest 2 prompt tokens: {inputs2.input_ids.shape[1]}")

with torch.no_grad():
    out2 = model.generate(
        **inputs2,
        max_new_tokens=128,
        temperature=0.3,
        do_sample=True,
        repetition_penalty=1.1,
    )
text2 = tok.decode(out2[0][inputs2.input_ids.shape[1] :], skip_special_tokens=True)
print(f"Test 2 output:\n{text2.strip()}")

print("\n=== ADAPTER SMOKE TEST PASSED ===")
