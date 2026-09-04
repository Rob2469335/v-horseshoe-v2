import re
t = open('/workspace/train_v4.py').read()
# CUDA not XPU
t = t.replace('device_map="xpu"', 'device_map="cuda"')
t = t.replace('torch.xpu.is_available()', 'torch.cuda.is_available()')
t = t.replace('torch.xpu.empty_cache()', 'torch.cuda.empty_cache()')
t = t.replace('torch.xpu.memory_allocated()', 'torch.cuda.memory_allocated()')
t = t.replace('torch.xpu.memory_reserved()', 'torch.cuda.memory_reserved()')
t = t.replace('from qlora_xpu_test', 'from torch')
# Fix the import for MemTrace
t = t.replace('import torch', 'import torch\ntorch.cuda.empty_cache() if torch.cuda.is_available() else None', 1)
open('/workspace/train_v4.py','w').write(t)
print("fixed xpu -> cuda")
