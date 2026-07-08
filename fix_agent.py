import pathlib, re, sys

POTENTIAL_PATHS = [
    r'C:\Users\rober\Projects\v-horseshoe-v2\runtime_v2\api\agent_service_v2.py',
    r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py',
    r'C:\Users\rober\Projects\v-horseshoe-v2\agent_service_v2_HEAD_snapshot.py',
]

target = None
for p in POTENTIAL_PATHS:
    if pathlib.Path(p).exists():
        target = pathlib.Path(p)
        break

if target is None:
    print("ERROR: No agent service file found at any known path.")
    sys.exit(1)

print(f"Patching: {target}")
src = target.read_text(encoding='utf-8')
original_src = src

# 1. Remove debug print lines
src = re.sub(r"^import sys; print\(f?'DEBUG:.*?'\)\n", "", src, flags=re.MULTILINE)

# 2. Deduplicate imports
for imp_mod in ["re", "json", "logging", "asyncio", "httpx"]:
    pattern = re.compile(rf"^import {imp_mod}\n(?:^import {imp_mod}\n)+", re.MULTILINE)
    src = pattern.sub(f"import {imp_mod}\n", src)

# 3. Harden the stream loop - skip done signal
# Use broader matching to handle whitespace variations
old_loop_pattern = re.compile(
    r'(\s+)async for _line in _r\.aiter_lines\(\):\n'
    r'\1    if not _line\.strip\(\): continue\n'
    r'\1    try:\n'
    r'\1        _evt = __import__\("json"\)\.loads\(_line\)\n'
    r'\1    except Exception: continue\n'
    r'\1    _piece = _evt\.get\("message", \{\}\)\.get\("content", ""\)\n'
    r'\1    if _piece:\n'
    r'\1        full_chunk_content \+= _piece\n'
    r'\1        model = chosen_model\n'
    r'\1        yield \{"content": _piece, "model": chosen_model, "trace_id": trace_id\}',
    re.DOTALL
)

def harden_loop(match):
    indent = match.group(1)
    new_loop = (
        f'{indent}async for _line in _r.aiter_lines():\n'
        f'{indent}    if not _line.strip(): continue\n'
        f'{indent}    try:\n'
        f'{indent}        _evt = __import__("json").loads(_line)\n'
        f'{indent}    except Exception: continue\n'
        f'{indent}    if _evt.get("done"): break\n'
        f'{indent}    _piece = _evt.get("message", {{}}).get("content", "")\n'
        f'{indent}    if _piece:\n'
        f'{indent}        full_chunk_content += _piece\n'
        f'{indent}        model = chosen_model\n'
        f'{indent}        yield {{"content": _piece, "model": chosen_model, "trace_id": trace_id}}'
    )
    return new_loop

src, loop_count = old_loop_pattern.subn(harden_loop, src)
if loop_count > 0:
    print(f"Stream loop hardened ({loop_count} match(es))")
else:
    print("Stream loop not matched - skipping")

# 4. Pick smarter default model based on agent role
old_model = re.compile(r'chosen_model\s*=\s*"qwen3:14b"')
def smarter_model(match):
    return '''_agent_role = agent.get("model_role", "fast")\n            chosen_model = "qwen3:14b" if _agent_role == "reasoning" else "qwen2.5:7b-instruct"'''

src, model_count = old_model.subn(smarter_model, src)
if model_count > 0:
    print(f"Model selection upgraded ({model_count} match(es))")
else:
    print("Model selection pattern not found - already upgraded or different format")

# Only write if changes were made
if src != original_src:
    target.write_text(src, encoding="utf-8")
    print("Changes written to disk")
else:
    print("No changes needed")

print("Done")
