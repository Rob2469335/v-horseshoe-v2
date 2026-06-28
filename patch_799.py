import pathlib, shutil, py_compile, sys

TARGET = pathlib.Path("swarm_os/services/agent_service.py")
BACKUP = TARGET.with_suffix(".py.bak_799")
import shutil as sh
sh.copy2(TARGET, BACKUP)

src = TARGET.read_text(encoding="utf-8")

OLD = "            mapped_tool_name, payload = _map_tool_call(tool_name, payload, available_tools, agent_id)"
NEW = "            mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)"

if OLD not in src:
    print("ERROR: not found - showing line 799 area:")
    for i, line in enumerate(src.splitlines()[795:805], 796):
        print("  {}: {!r}".format(i, line))
    sys.exit(1)

src = src.replace(OLD, NEW, 1)
TARGET.write_text(src, encoding="utf-8")
py_compile.compile(str(TARGET), doraise=True)
print("Fixed and syntax PASSED")
