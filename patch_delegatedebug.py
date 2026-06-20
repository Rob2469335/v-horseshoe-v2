path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '            if mapped_tool_name == "__delegate__":\n                # Model tried to delegate — treat as a final response using the payload description\n                delegate_msg = payload.get("message") or payload.get("task") or payload.get("content") or str(payload)'

new = '            if mapped_tool_name == "__delegate__":\n                # Model tried to delegate — treat as a final response using the payload description\n                logger.warning(f"[DELEGATE PAYLOAD] {payload}")\n                delegate_msg = payload.get("message") or payload.get("task") or payload.get("content") or payload.get("target_agent") or payload.get("instruction") or str(payload)'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched OK')
else:
    print('ERROR: not found')
