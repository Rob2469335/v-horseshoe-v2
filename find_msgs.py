path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines, start=1):
    if 'system_msg' in line or 'build_ctx' in line or 'messages = ' in line:
        print(f'{i}: {repr(line)}')
