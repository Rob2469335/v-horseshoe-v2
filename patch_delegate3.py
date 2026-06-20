path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '        elif tool_name in {"search", "google_search", "web", "search_web"}:\n            mapped_name = "web_search"'

new = '        elif tool_name in {"search", "google_search", "web", "search_web"}:\n            mapped_name = "web_search"\n        elif tool_name in {"delegate", "handoff", "transfer", "route"}:\n            mapped_name = "__delegate__"'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched OK')
else:
    print('ERROR: not found')
