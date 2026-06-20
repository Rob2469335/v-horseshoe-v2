path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '                    layout.add_row(Panel(Markdown(display), border_style="bright_blue dim", padding=(0, 1)))'
new = '                    layout.add_row(Panel(Markdown(display) if callable(Markdown) else display, border_style="bright_blue dim", padding=(0, 1)))'

# Actually just force it to use the module-level import directly
src = src.replace(
    '                    layout.add_row(Panel(Markdown(display), border_style="bright_blue dim", padding=(0, 1)))',
    '                    from rich.markdown import Markdown as _MD\n                    layout.add_row(Panel(_MD(display), border_style="bright_blue dim", padding=(0, 1)))'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Patched')
