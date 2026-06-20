import pathlib, re

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py')
src = p.read_text(encoding='utf-8')

# 1. Remove triple debug print lines
src = re.sub(r"import sys; print\(f'DEBUG: Loading agent_service from: \{__file__\}'\)\n", "", src)

# 2. Remove duplicate imports
src = re.sub(r"import re\nimport re\n", "import re\n", src)
src = re.sub(r"import json\nimport json\n", "import json\n", src)

# 3. Harden the stream loop - skip done signal, handle empty pieces
old_loop = """                    async for _line in _r.aiter_lines():
                        if not _line.strip(): continue
                        try:
                            _evt = __import__("json").loads(_line)
                        except Exception: continue
                        _piece = _evt.get("message", {}).get("content", "")
                        if _piece:
                            full_chunk_content += _piece
                            model = chosen_model
                            yield {"content": _piece, "model": chosen_model, "trace_id": trace_id}"""

new_loop = """                    async for _line in _r.aiter_lines():
                        if not _line.strip(): continue
                        try:
                            _evt = __import__("json").loads(_line)
                        except Exception: continue
                        if _evt.get("done"): break
                        _piece = _evt.get("message", {}).get("content", "")
                        if _piece:
                            full_chunk_content += _piece
                            model = chosen_model
                            yield {"content": _piece, "model": chosen_model, "trace_id": trace_id}"""

if old_loop in src:
    src = src.replace(old_loop, new_loop)
    print("Stream loop hardened OK")
else:
    print("Stream loop not matched - skipping")

# 4. Pick smarter default model based on agent role
old_model = '            chosen_model = "qwen3:14b"'
new_model = '''            _agent_role = agent.get("model_role", "fast")
            chosen_model = "qwen3:14b" if _agent_role == "reasoning" else "qwen2.5:7b-instruct"'''
src = src.replace(old_model, new_model)

p.write_text(src, encoding="utf-8")
print("Done")
