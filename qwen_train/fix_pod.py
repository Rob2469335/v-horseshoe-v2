import re
t = open('/workspace/train_v4.py').read()
t = re.sub(r'MODEL_PATH = r".*?"', 'MODEL_PATH = "/workspace/hf_cache/models--Qwen--Qwen3.5-4B/snapshots/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"', t)
t = re.sub(r'OUTPUT_DIR = r".*?"', 'OUTPUT_DIR = "/workspace/v6_adapter"', t)
t = re.sub(r'MAX_LEN = \d+.*', 'MAX_LEN = 2528  # RTX 3090 24GB - full context', t)
open('/workspace/train_v4.py','w').write(t)
print("fixed")
