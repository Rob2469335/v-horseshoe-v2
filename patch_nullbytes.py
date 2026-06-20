path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'rb') as f:
    raw = f.read()

cleaned = raw.replace(b'\x00', b'')

with open(path, 'wb') as f:
    f.write(cleaned)

print(f'Removed {raw.count(b"\x00")} null bytes')
