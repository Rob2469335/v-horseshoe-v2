path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '                if chunk_type == "final":\n                    live.stop()\n                    final_content = chunk.get("content", "")\n                    if isinstance(final_content, dict):\n                        ctx.console.print_json(data=final_content)\n                    else:\n                        from rich.markdown import Markdown\n                        ctx.console.print(Markdown(str(final_content)))\n                    return history'

new = '                if chunk_type == "final":\n                    live.stop()\n                    final_content = chunk.get("content", "")\n                    if isinstance(final_content, dict):\n                        ctx.console.print_json(data=final_content)\n                    else:\n                        from rich.markdown import Markdown as _Markdown\n                        ctx.console.print(_Markdown(str(final_content)))\n                    return history'

if old in src:
    src = src.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    print('Patched')
else:
    print('ERROR: not found')
