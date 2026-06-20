import pathlib, re

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py')
src = p.read_text(encoding='utf-8')

old = '            async for chunk, m, tid in self.orchestrator.stream_generate(model=None, messages=messages):\n                full_chunk_content += chunk\n                model = m\n                trace_id = tid\n                yield {"content": chunk, "model": m, "trace_id": tid}'

new = '            import httpx, uuid\n            chosen_model = "qwen3:14b"\n            trace_id = str(uuid.uuid4())[:8]\n            async with httpx.AsyncClient(timeout=300.0) as _client:\n                async with _client.stream("POST", "http://127.0.0.1:11434/api/chat", json={"model": chosen_model, "messages": messages, "stream": True}) as _r:\n                    async for _line in _r.aiter_lines():\n                        if not _line.strip(): continue\n                        try:\n                            _evt = __import__("json").loads(_line)\n                        except Exception: continue\n                        _piece = _evt.get("message", {}).get("content", "")\n                        if _piece:\n                            full_chunk_content += _piece\n                            model = chosen_model\n                            yield {"content": _piece, "model": chosen_model, "trace_id": trace_id}'

if old in src:
    src = src.replace(old, new)
    p.write_text(src, encoding='utf-8')
    print("OK")
else:
    print("NOT FOUND")
    # show actual lines around stream_generate
    for i, line in enumerate(src.splitlines()):
        if "stream_generate" in line or "full_chunk_content" in line:
            print(f"{i+1}: {repr(line)}")
