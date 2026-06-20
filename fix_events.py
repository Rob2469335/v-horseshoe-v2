import pathlib, re

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py')
src = p.read_text(encoding='utf-8')

# Add event_bus import after existing imports
old_import = 'from swarm_os.agent_runtime import AgentRuntime'
new_import = '''from swarm_os.agent_runtime import AgentRuntime
from swarm_os.core.event_bus import event_bus
import uuid as _uuid
import time as _time'''

if old_import in src:
    src = src.replace(old_import, new_import)
    print("Imports added OK")
else:
    print("Import line not found")

# Emit event when stream starts
old_stream_start = '''        for _ in range(5):
            full_chunk_content = ""
            model = ""
            trace_id = ""'''

new_stream_start = '''        session_id = str(_uuid.uuid4())[:8]
        event_bus.emit("AGENT_START", session_id, {
            "agent_id": agent_id,
            "prompt": prompt[:200],
            "timestamp": _time.time()
        })

        for _ in range(5):
            full_chunk_content = ""
            model = ""
            trace_id = ""'''

if old_stream_start in src:
    src = src.replace(old_stream_start, new_stream_start)
    print("Stream start event OK")
else:
    print("Stream start not matched")

# Emit event after full response received
old_after_stream = '''            messages.append({"role": "assistant", "content": full_chunk_content})

            # --- SINGULARITY: Visual Telemetry ---'''

new_after_stream = '''            messages.append({"role": "assistant", "content": full_chunk_content})

            # Emit response event to organism
            event_bus.emit("AGENT_RESPONSE", session_id, {
                "agent_id": agent_id,
                "model": model,
                "content_length": len(full_chunk_content),
                "timestamp": _time.time(),
                "learning_outcome": {"result": "success"}
            })

            # --- SINGULARITY: Visual Telemetry ---'''

if old_after_stream in src:
    src = src.replace(old_after_stream, new_after_stream)
    print("Response event OK")
else:
    print("After stream not matched")

# Emit tool call events
old_tool_yield = '''            yield {"content": f"\\n\\n[System: Executing tool \'{tool_name}\'...]\\n", "model": model, "trace_id": trace_id}'''

new_tool_yield = '''            event_bus.emit("TOOL_CALL", session_id, {
                "agent_id": agent_id,
                "tool": tool_name,
                "timestamp": _time.time()
            })
            yield {"content": f"\\n\\n[System: Executing tool \'{tool_name}\'...]\\n", "model": model, "trace_id": trace_id}'''

if old_tool_yield in src:
    src = src.replace(old_tool_yield, new_tool_yield)
    print("Tool call event OK")
else:
    print("Tool yield not matched")

# Emit tool result event
old_tool_obs = '''                obs = f"\\nObservation: {json.dumps(result)}"
                messages.append({"role": "user", "content": obs})
                yield {"content": obs + "\\n\\n", "model": model, "trace_id": trace_id}'''

new_tool_obs = '''                obs = f"\\nObservation: {json.dumps(result)}"
                messages.append({"role": "user", "content": obs})
                event_bus.emit("TOOL_RESULT", session_id, {
                    "agent_id": agent_id,
                    "tool": tool_name,
                    "ok": result.get("ok", True),
                    "timestamp": _time.time(),
                    "learning_outcome": {"result": "success" if result.get("ok", True) else "fail"}
                })
                yield {"content": obs + "\\n\\n", "model": model, "trace_id": trace_id}'''

if old_tool_obs in src:
    src = src.replace(old_tool_obs, new_tool_obs)
    print("Tool result event OK")
else:
    print("Tool obs not matched")

p.write_text(src, encoding='utf-8')
print("Done - agent_service.py wired to event_bus")
