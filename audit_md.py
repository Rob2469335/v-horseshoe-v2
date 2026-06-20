import ast, sys
path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()
try:
    ast.parse(src)
    print('Syntax OK')
except SyntaxError as e:
    print(f'Syntax error: {e}')

# Find all Markdown references
for i, line in enumerate(src.splitlines(), start=1):
    if 'Markdown' in line:
        print(f'{i}: {repr(line)}')
