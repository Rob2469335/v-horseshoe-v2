path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# Remove top-level Markdown import
src = src.replace('from rich.markdown import Markdown\n', '')

# Replace all uses of bare Markdown( with _MD( after local import
src = src.replace(
    '                    from rich.markdown import Markdown as _MD\n                    layout.add_row(Panel(_MD(display)',
    '                    from rich.markdown import Markdown as _MD\n                    layout.add_row(Panel(_MD(display)'
)

# Fix any remaining bare Markdown( references
import re
src = re.sub(r'(?<!_)(?<!as )Markdown\(', '_MD(', src)

# Add _MD import at the one place it is used in final handler
src = src.replace(
    'ctx.console.print(Markdown(str(final_content)))',
    'from rich.markdown import Markdown as _MD; ctx.console.print(_MD(str(final_content)))'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
