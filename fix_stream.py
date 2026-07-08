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

old = '            async for chunk, m, tid in self.orchestrator.stream_generate(model=None, messages=messages):\n                full_chunk_content += chunk\n                model = m\n                trace_id = tid\n                yield {"content": chunk, "model": m, "trace_id": tid}'

new = '            import httpx, uuid\n            chosen_model = "qwen3:14b"\n            trace_id = str(uuid.uuid4())[:8]\n            async with httpx.AsyncClient(timeout=300.0) as _client:\n                async with _client.stream("POST", "http://127.0.0.1:11434/api/chat", json={"model": chosen_model, "messages": messages, "stream": True}) as _r:\n                    async for _line in _r.aiter_lines():\n                        if not _line.strip(): continue\n                        try:\n                            _evt = __import__("json").loads(_line)\n                        except Exception: continue\n                        _piece = _evt.get("message", {}).get("content", "")\n                        if _piece:\n                            full_chunk_content += _piece\n                            model = chosen_model\n                            yield {"content": _piece, "model": chosen_model, "trace_id": trace_id}'

if old in src:
    src = src.replace(old, new)
    changed = True
    print("Stream inline patch applied")
else:
    print("Stream pattern not found - already patched or different format")
    changed = False

# Also try a more flexible regex-based approach for already-modified patterns
loop_pattern = re.compile(
    r'async for _line in _r\.aiter_lines\(\):\s*'
    r'if not _line\.strip\(\): continue\s*'
    r'try:\s*'
    r'_evt = __import__\("json"\)\.loads\(_line\)\s*'
    r'except Exception: continue'
)
if loop_pattern.search(src) and "done" not in src.split("aiter_lines")[1].split("\n", 1)[0] if "aiter_lines" in src else True:
    # Already has the new pattern, no need to re-patch
    pass

if src != original_src:
    target.write_text(src, encoding='utf-8')
    print("Changes written to disk")
else:
    print("No changes needed")

print("Done")
