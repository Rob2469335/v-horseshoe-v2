import re
p = '/workspace/train_v4.py'
t = open(p).read()
# Fix model path to use HF cache
t = re.sub(r'MODEL_PATH = r\".*?\"', 'MODEL_PATH = \"/workspace/hf_cache/hub/Qwen--Qwen3.5-4B\"', t)
# Check the actual cached path
open(p, 'w').write(t)
