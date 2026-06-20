path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = """            if mapped_tool_name == "__delegate__":
                # Model tried to delegate — treat as a final response using the payload description
                logger.warning(f"[DELEGATE PAYLOAD] {payload}")
                delegate_msg = payload.get("message") or payload.get("task") or payload.get("content") or payload.get("target_agent") or payload.get("instruction") or str(payload)
                logger.info(f"[DELEGATE] Model '{active_model}' delegated: {delegate_msg}")
                yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": delegate_msg}
                return"""

new = """            if mapped_tool_name == "__delegate__":
                logger.warning(f"[DELEGATE PAYLOAD] {payload}")
                target = payload.get("target_agent", "")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)
                delegate_msg = f"**Delegating to {target}**\\n\\n{task}" if target else task
                logger.info(f"[DELEGATE] Model '{active_model}' delegated to {target}: {task}")
                yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": delegate_msg}
                return"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
