import re
path = r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = "    json_str = re.sub(r',\\s*([\\]}])', r'\\1', json_str)\n    return json_str"

new = """    json_str = re.sub(r',\\s*([\\]}])', r'\\1', json_str)
    # Strip extra trailing data after first valid JSON object/array
    if json_str.startswith('{'):
        depth = 0
        for i, ch in enumerate(json_str):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_str = json_str[:i+1]
                    break
    elif json_str.startswith('['):
        depth = 0
        for i, ch in enumerate(json_str):
            if ch == '[': depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    json_str = json_str[:i+1]
                    break
    return json_str"""

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched successfully')
else:
    print('ERROR: not found - printing actual lines for inspection')
    for i, line in enumerate(src.splitlines()[53:62], start=54):
        print(f'{i}: {repr(line)}')
