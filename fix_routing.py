import pathlib

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py')
src = p.read_text(encoding='utf-8')

# Fix 1: route commands typed at the prompt through the command handler
old_else = '''            else:
                history = stream_prompt(selected_agent, cmd_line, history)'''

new_else = '''            elif cmd in ("status", "resources", "models", "heal", "traces", "help", "list", "clear", "boot", "exit", "quit"):
                pass  # already handled above
            else:
                history = stream_prompt(selected_agent, cmd_line, history)'''

# Actually the real fix - reorder so commands are caught before fallthrough
old_fallthrough = '''            if cmd == "boot":'''
new_fallthrough = '''            if cmd == "help":
                cmd_help()
            elif cmd == "status":
                cmd_status()
            elif cmd == "resources":
                cmd_resources()
            elif cmd == "models":
                cmd_models()
            elif cmd == "heal":
                cmd_heal()
            elif cmd == "traces":
                cmd_traces()
            elif cmd == "boot":'''

if old_fallthrough in src:
    src = src.replace(old_fallthrough, new_fallthrough)
    print("Command routing fixed OK")
else:
    print("NOT FOUND")

# Fix 2: remove duplicate final panel - only show once
old_final = '''    # Final render - clean single output
    console.print(Rule(style="dim white"))
    final = re.sub(r\'<plan>.*?</plan>\', \'\', full_content, flags=re.DOTALL)
    final = re.sub(r\'<tool_call[^>]*>.*?</tool_call>\', \'\', final, flags=re.DOTALL).strip()

    plan_match = re.search(r\'<plan>(.*?)</plan>\', full_content, re.DOTALL)
    if plan_match:
        console.print(Panel(
            Text(plan_match.group(1).strip(), style="italic yellow"),
            title="[bold yellow]Strategic Plan[/bold yellow]",
            border_style="yellow"
        ))

    if tool_calls_made:
        console.print(f"[dim]Tools used: {\', \'.join(tool_calls_made)}[/dim]")

    console.print(Panel(
        Markdown(final) if final else Text("[dim]No response[/dim]"),
        border_style="bright_cyan",
        padding=(0, 1)
    ))
    console.print()'''

new_final = '''    console.print()
    if tool_calls_made:
        console.print(f"[dim]Tools used: {', '.join(tool_calls_made)}[/dim]")
    console.print()'''

if old_final in src:
    src = src.replace(old_final, new_final)
    print("Duplicate panel removed OK")
else:
    print("Final panel not matched")

p.write_text(src, encoding='utf-8')
