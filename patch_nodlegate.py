path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '            "- delegate: {target_agent: \'planner\'|\'executor\'|\'tool-runner\', task: \'...\'}",\n'
new = ''

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched — delegate removed from system prompt')
else:
    print('ERROR: not found')
