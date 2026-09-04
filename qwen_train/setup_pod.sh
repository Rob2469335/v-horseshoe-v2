#!/bin/bash
set -e
echo "=== installing deps ==="
pip install peft transformers bitsandbytes accelerate datasets -q --break-system-packages
echo "=== deps installed ==="
echo "=== downloading base model ==="
python3 -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
import os
cache = '/workspace/hf_cache'
print('downloading tokenizer...')
AutoTokenizer.from_pretrained('Qwen/Qwen3.5-4B', cache_dir=cache)
print('downloading model...')
AutoModelForCausalLM.from_pretrained('Qwen/Qwen3.5-4B', cache_dir=cache, torch_dtype='auto')
print('DONE')
"
echo "=== model cached ==="
