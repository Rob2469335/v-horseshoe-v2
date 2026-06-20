path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = """            if mapped_tool_name not in available_tools:
                logger.warning(f"[TOOL CALL FAILURE] Model '{active_model}' called unknown tool '{tool_name}' (mapped to '{mapped_tool_name}')")
                yield {"agent_id": agent_id, "type": "model_escalation", "from_model": active_model, "reason": f"unknown_tool: {mapped_tool_name}"}
                active_model = None
                continue"""

new = """            if mapped_tool_name == "__delegate__":
                # Model tried to delegate — treat as a final response using the payload description
                delegate_msg = payload.get("message") or payload.get("task") or payload.get("content") or str(payload)
                logger.info(f"[DELEGATE] Model '{active_model}' delegated: {delegate_msg}")
                yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": delegate_msg}
                return
            if mapped_tool_name not in available_tools:
                logger.warning(f"[TOOL CALL FAILURE] Model '{active_model}' called unknown tool '{tool_name}' (mapped to '{mapped_tool_name}')")
                yield {"agent_id": agent_id, "type": "model_escalation", "from_model": active_model, "reason": f"unknown_tool: {mapped_tool_name}"}
                active_model = None
                continue"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched delegate handler OK')
else:
    print('ERROR: not found')
