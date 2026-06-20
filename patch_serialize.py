path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = 'messages.append({"role": "user", "content": f"Observation: {json.dumps(result, ensure_ascii=False)}"})' 

new = '''# Serialize tool result safely — handle dataclasses, Pydantic models, and custom response objects
            def _serialize(obj):
                if isinstance(obj, dict): return obj
                if hasattr(obj, "model_dump"): return obj.model_dump()
                if hasattr(obj, "__dict__"): return obj.__dict__
                return str(obj)
            messages.append({"role": "user", "content": f"Observation: {json.dumps(_serialize(result), ensure_ascii=False)}"})'''

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
