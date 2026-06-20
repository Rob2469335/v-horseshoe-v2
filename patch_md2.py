path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# Add Markdown import at top if not already there
if 'from rich.markdown import Markdown' not in src:
    src = src.replace('from rich.prompt import Prompt', 'from rich.markdown import Markdown\nfrom rich.prompt import Prompt')
    print('Added Markdown import')

# Fix the inline import to use the top-level one
src = src.replace(
    'from rich.markdown import Markdown as _Markdown\n                        ctx.console.print(_Markdown(str(final_content)))',
    'ctx.console.print(Markdown(str(final_content)))'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
