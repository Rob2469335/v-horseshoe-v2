path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

src = src.replace(
    'ctx.state.history.append({\n                        "agent_id": agent_id,\n                        "prompt": prompt,\n                        "response": full_content,\n                        "timestamp": time.time()\n                    })\n                    ctx.state.history_pointer = len(ctx.state.history) - 1\n                    ctx.state.save()',
    'ctx.history_pointer = len(ctx.history) - 1\n                    ctx.save()'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
