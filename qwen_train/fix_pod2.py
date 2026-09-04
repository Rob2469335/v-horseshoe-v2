import re
t = open('/workspace/train_v4.py').read()
t = re.sub(r'MODEL_PATH = ".*?"', 'MODEL_PATH = "/workspace/hf_cache/models--Qwen--Qwen3.5-4B/snapshots/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a"', t)
open('/workspace/train_v4.py','w').write(t)
print("fixed to snapshot path")
