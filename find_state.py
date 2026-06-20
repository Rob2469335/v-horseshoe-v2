path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines[283:293], start=284):
    print(f'{i}: {repr(line)}')
