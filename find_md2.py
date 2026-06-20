path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the stream_prompt function and print all lines mentioning Markdown or _MD
in_func = False
for i, line in enumerate(lines, start=1):
    if 'def stream_prompt' in line:
        in_func = True
    if in_func and ('Markdown' in line or '_MD' in line):
        print(f'{i}: {repr(line)}')
    if in_func and i > 100 and 'def ' in line and 'stream_prompt' not in line:
        break
