import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
BACKUP = TARGET.with_suffix(".py.bak_askuser")
import shutil as sh
sh.copy2(TARGET, BACKUP)

src = TARGET.read_text(encoding="utf-8")

# The early ask_user block we added should fire, but maybe it's not in the file.
# Check if it exists:
if "Early ask_user block" in src:
    print("Early ask_user block IS present")
else:
    print("Early ask_user block MISSING - adding now")
    OLD = """            is_duplicate = False
            if not is_control_turn:"""
    NEW = """            # Early ask_user block: catch ask_user before duplicate logic
            if agent_id != "coordinator" and 'name="ask_user"' in full_chunk_content:
                logger.warning(f"ask_user blocked early for non-coordinator: {agent_id}")
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": "Only the coordinator agent is allowed to ask the user questions.",
                }
                return

            is_duplicate = False
            if not is_control_turn:"""
    if OLD not in src:
        print("ERROR: is_duplicate anchor not found")
        sys.exit(1)
    src = src.replace(OLD, NEW, 1)
    print("Early ask_user block added")

TARGET.write_text(src, encoding="utf-8")
py_compile.compile(str(TARGET), doraise=True)
print("Syntax PASSED")
