import pathlib

# Fix 1: Throttle the Live display in cli.py - only update every N chunks
p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py')
src = p.read_text(encoding='utf-8')

old = '    with Live(render_swarm_pulse(agent_id, model, trace_id), refresh_per_second=10, console=console, vertical_overflow="visible") as live:'
new = '    with Live(render_swarm_pulse(agent_id, model, trace_id), refresh_per_second=2, console=console, vertical_overflow="visible") as live:'
src = src.replace(old, new)

# Fix 2: Buffer chunks before updating display
old2 = '''                    full_content += content

                    layout = Table.grid(expand=True)'''
new2 = '''                    full_content += content

                    if len(full_content) % 80 > 40:
                        continue

                    layout = Table.grid(expand=True)'''
src = src.replace(old2, new2)

p.write_text(src, encoding='utf-8')
print("cli.py patched")

# Fix 3: Use qwen2.5:7b for coordinator (better tool use), 3b for others
p2 = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py')
src2 = p2.read_text(encoding='utf-8')
old3 = '"qwen2.5:3b-instruct" if _agent_role == "reasoning" else "qwen2.5:3b-instruct"'
new3 = '"qwen2.5:7b-instruct" if _agent_role == "reasoning" else "qwen2.5:3b-instruct"'
if old3 in src2:
    src2 = src2.replace(old3, new3)
    p2.write_text(src2, encoding='utf-8')
    print("agent_service.py model updated")
else:
    print("model line not found in agent_service.py")
