#!/bin/bash
set -e
cd /workspace
export V4_DATA_FILE=/workspace/v6_traces.jsonl
export V4_OUTPUT_DIR=/workspace/v6_adapter
export V4_PROBE_STEPS=0
export V4_MEM_TRACE=1
export HF_HOME=/workspace/hf_cache
export TRANSFORMERS_CACHE=/workspace/hf_cache

echo "=== starting V6 training on RTX 3090 (24GB) ==="
echo "MAX_LEN=2528, batch=1, LoRA r=8, epochs=5"
python3 train_v4.py 2>&1 | tee /workspace/train_log.txt
echo "=== training complete ==="
