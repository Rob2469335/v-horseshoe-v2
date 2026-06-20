from pathlib import Path

file = Path("swarm_os/core/orchestrator.py")

lines = file.read_text().splitlines()

future = [l for l in lines if "from __future__ import" in l]
lines = [l for l in lines if "from __future__ import" not in l]

insert_at = 0
if lines and lines[0].strip().startswith("""\"\"\""""):
    insert_at = 1

if future:
    lines.insert(insert_at, future[0])

file.write_text("\n".join(lines))

print("FIXED: __future__ import repositioned safely")
