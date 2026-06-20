import re, pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\api\routes.py')
src = p.read_text(encoding='utf-8')

old1 = re.compile(r'async def _safe_ollama_reachable\(runtime: Any\) -> bool:.*?except Exception: return False', re.DOTALL)
new1 = 'async def _safe_ollama_reachable(runtime: Any) -> bool:\n    try:\n        async with httpx.AsyncClient(timeout=3.0) as client:\n            r = await client.get("http://127.0.0.1:11434/api/tags")\n            return r.status_code == 200\n    except Exception: return False'

old2 = re.compile(r'async def _safe_ollama_models\(runtime: Any\) -> list\[str\]:.*?except Exception: return \[\]', re.DOTALL)
new2 = 'async def _safe_ollama_models(runtime: Any) -> list[str]:\n    try:\n        async with httpx.AsyncClient(timeout=3.0) as client:\n            r = await client.get("http://127.0.0.1:11434/api/tags")\n            data = r.json()\n            return sorted({m["name"] for m in data.get("models", []) if m.get("name")})\n    except Exception: return []'

src, n1 = old1.subn(new1, src, count=1)
src, n2 = old2.subn(new2, src, count=1)
p.write_text(src, encoding='utf-8')
print(f'Replaced: reachable={n1}, models={n2}')
