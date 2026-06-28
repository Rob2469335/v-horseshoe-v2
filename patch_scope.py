import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
if not TARGET.exists():
    print("ERROR: not found"); sys.exit(1)

BACKUP = TARGET.with_suffix(".py.bak_scope")
shutil.copy2(TARGET, BACKUP)
print("Backup -> {}".format(BACKUP))

src = TARGET.read_text(encoding="utf-8")
original = src

# Revert the _map_tool_call change - restore reconcile calls
OLD1 = """            available_tools = set(runtime.list_tools())
            mapped_tool_name, payload = _map_tool_call(tool_name, payload, available_tools, agent_id)
            repair_meta = {
                "requested_tool": tool_name,
                "resolved_tool": mapped_tool_name,
                "repaired": mapped_tool_name != tool_name,
                "repair_mode": False,
            }"""

NEW1 = """            available_tools = set(runtime.list_tools())
            try:
                mapped_tool_name, payload, repair_meta = reconcile_and_repair_tool_call(tool_name, payload, available_tools, agent_id)
            except (TypeError, ValueError):
                mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)
                repair_meta = {
                    "requested_tool": tool_name,
                    "resolved_tool": mapped_tool_name,
                    "repaired": mapped_tool_name != tool_name,
                    "repair_mode": False,
                }"""

if OLD1 not in src:
    print("ERROR: scope-fix anchor not found")
    sys.exit(1)

src = src.replace(OLD1, NEW1, 1)
print("Fix 1 applied: restored reconcile calls")

# FIX 2: The ask_user test fails because reconcile returns 2-tuple but line 797
# tries to unpack 3 values -> TypeError -> falls to line 799 which works fine.
# mapped_tool_name becomes "ask_user". VIRTUAL_TOOLS check passes.
# ask_user check fires and yields final with correct content.
# BUT the test finds an EMPTY final chunk first.
# The empty final comes from: the mock also streams a done=True event which
# the Ollama parser sees as end-of-stream with piece="", then full_chunk_content
# ends up empty on a second turn.
# Real fix: ensure the ask_user final chunk content is never empty by
# making the yield content non-empty even if something goes wrong.
# Actually the simplest fix: move the ask_user check BEFORE the duplicate check
# so it fires before any empty final can be emitted.
# The ask_user block is currently after is_duplicate. Move it before.

# Find the is_duplicate block start and the ask_user check
# Current order:
#   1. is_duplicate check -> may yield final and return
#   2. match = re.search(...)
#   3. if not match: delegate_tag / shorthand / final
#   4. else: tool_name, raw_payload = ...
#   5. available_tools / reconcile
#   6. VIRTUAL_TOOLS check
#   7. ask_user check  <- this is where we want to be earlier
#
# The ask_user check needs mapped_tool_name which needs reconcile which needs match.
# So we can't move it before match. But we CAN move it before is_duplicate
# by doing a quick pre-check on the raw content string.
# If full_chunk_content contains 'ask_user' and agent is not coordinator -> block early.

OLD2 = """            is_duplicate = False
            if not is_control_turn:
                for prev in previous_outputs:
                    if clean_content == prev:
                        is_duplicate = True
                        break
                    words1 = set(clean_content.lower().split())
                    words2 = set(prev.lower().split())
                    if words1 and words2:
                        overlap = len(words1 & words2) / max(len(words1), len(words2))
                        if overlap > 0.85:
                            is_duplicate = True
                            break

            if is_duplicate:
                logger.warning(f"Detected duplicate or near-identical assistant output in turn {turn}. Terminating loop.")
                if full_chunk_content.strip():
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": full_chunk_content,
                    }
                return

            previous_outputs.append(clean_content)"""

NEW2 = """            # Early ask_user block: if content has ask_user tool and agent is not coordinator,
            # block it immediately before any duplicate/final logic fires.
            if agent_id != "coordinator" and 'name="ask_user"' in full_chunk_content:
                logger.warning(f"ask_user blocked early for non-coordinator agent: {agent_id}")
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": "Only the coordinator agent is allowed to ask the user questions.",
                }
                return

            is_duplicate = False
            if not is_control_turn:
                for prev in previous_outputs:
                    if clean_content == prev:
                        is_duplicate = True
                        break
                    words1 = set(clean_content.lower().split())
                    words2 = set(prev.lower().split())
                    if words1 and words2:
                        overlap = len(words1 & words2) / max(len(words1), len(words2))
                        if overlap > 0.85:
                            is_duplicate = True
                            break

            if is_duplicate:
                logger.warning(f"Detected duplicate or near-identical assistant output in turn {turn}. Terminating loop.")
                if full_chunk_content.strip():
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": full_chunk_content,
                    }
                return

            previous_outputs.append(clean_content)"""

if OLD2 not in src:
    print("ERROR: is_duplicate anchor not found")
    sys.exit(1)

src = src.replace(OLD2, NEW2, 1)
print("Fix 2 applied: early ask_user block before duplicate check")

TARGET.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax PASSED")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR - restoring: {}".format(e))
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)

if src == original:
    print("WARNING: no changes made")
else:
    print("Done")
