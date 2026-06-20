path = r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py"

with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# Remove the duplicate line
del lines[130]

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Duplicate line removed")
