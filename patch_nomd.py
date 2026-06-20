path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '                if display:\n                    from rich.markdown import Markdown as _MD\n                    layout.add_row(Panel(_MD(display), border_style="bright_blue dim", padding=(0, 1)))'
new = '                if display:\n                    layout.add_row(Panel(display, border_style="bright_blue dim", padding=(0, 1)))'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
