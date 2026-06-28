import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
if not TARGET.exists():
    print("ERROR: not found"); sys.exit(1)

BACKUP = TARGET.with_suffix(".py.bak_line799")
shutil.copy2(TARGET, BACKUP)

lines = TARGET.read_text(encoding="utf-8").splitlines(keepends=True)
print("Total lines: {}".format(len(lines)))

# Fix line 799 (index 798): replace _map_tool_call with reconcile call
target_line = lines[798]
print("Line 799 current: {}".format(target_line.rstrip()))

if "_map_tool_call" not in target_line:
    print("ERROR: _map_tool_call not on line 799")
    sys.exit(1)

lines[798] = "                mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)\n"
print("Line 799 fixed:   {}".format(lines[798].rstrip()))

src = "".join(lines)
TARGET.write_text(src, encoding="utf-8")

try:
    py_compile.compile(str(TARGET), doraise=True)
    print("Syntax PASSED")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR - restoring: {}".format(e))
    import shutil
    shutil.copy2(BACKUP, TARGET)
    sys.exit(1)

print("Done")
