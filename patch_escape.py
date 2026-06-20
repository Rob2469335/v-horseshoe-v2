import re
path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = "def repair_json_string(json_str: str) -> str:\n    json_str = json_str.strip()"

new = """def repair_json_string(json_str: str) -> str:
    json_str = json_str.strip()
    # Fix invalid escape sequences from model outputs (e.g. \\p, \\e, \\s)
    json_str = re.sub(r'\\\\(?!["\\\\/bfnrtu0-9])', r'\\\\\\\\', json_str)"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched successfully')
else:
    print('ERROR: not found - printing actual lines')
    for i, line in enumerate(src.splitlines()[53:62], start=54):
        print(f'{i}: {repr(line)}')
