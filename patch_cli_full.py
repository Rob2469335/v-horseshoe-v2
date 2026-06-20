path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# Fix 1: ctx.state.history -> ctx.history (ctx IS the state)
src = src.replace(
    '''                    # Record execution run to history
                    ctx.state.history.append({
                        "agent_id": agent_id,
                        "prompt": prompt,
                        "response": full_content,
                        "timestamp": time.time()
                    })
                    ctx.state.history_pointer = len(ctx.state.history) - 1
                    ctx.state.save()
                    return history''',
    '''                    # Record execution run to history
                    ctx.history_pointer = len(ctx.history) - 1
                    ctx.save()
                    return history'''
)

# Fix 2: Add agent_handoff chunk handler before tool_result
src = src.replace(
    '                if chunk_type == "tool_result":',
    '''                if chunk_type == "agent_handoff":
                    live.stop()
                    from_a = chunk.get("from", agent_id)
                    to_a = chunk.get("to", "executor")
                    task = str(chunk.get("task", ""))[:80]
                    ctx.console.print(render_step_micro_ui("swarm", f"{from_a} → {to_a}: {task}"))
                    live.start()
                    continue

                if chunk_type == "tool_result":'''
)

# Fix 3: debate loop uses wrong key - fix proposal/critique extraction
src = src.replace(
    '    proposal = state.history[-1]["response"] if state.history else ""',
    '    proposal = ctx.history[-1].get("content", "") if ctx.history else ""'
)
src = src.replace(
    '    critique = state.history[-1]["response"] if state.history else ""',
    '    critique = ctx.history[-1].get("content", "") if ctx.history else ""'
)

# Fix 4: Add /clear and /agents commands at end before if __name__
clear_cmd = '''
@registry.register("clear", "Clear session history and reset context", aliases=["reset"])
def cmd_clear(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.history = []
    ctx.state.history_pointer = -1
    ctx.state.current_topic = "Nexus Initialization"
    ctx.state.current_summary = "Establishing connection to Zenith Swarm OS..."
    ctx.state.strategic_intent = ""
    ctx.state.delegation_chain = [ctx.state.active_agent]
    ctx.state.save()
    ctx.console.print("[green]✓ Session history cleared. Context reset.[/green]")


@registry.register("agents", "List all registered agents and their roles")
def cmd_agents(ctx: CommandContext, args: List[str]) -> None:
    resp = ctx.call_api("/agents", "GET")
    if not resp:
        ctx.console.print("[bold red]✗ Backend offline[/bold red]")
        return
    try:
        agents = resp.json()
        table = Table(box=SIMPLE, header_style="bold cyan")
        table.add_column("Agent ID", style="bold green")
        table.add_column("Role", style="cyan")
        table.add_column("Model Role", style="yellow")
        table.add_column("Description", style="white")
        for a in agents:
            table.add_row(
                a.get("id", "?"),
                a.get("role", "?"),
                a.get("model_role", "?"),
                a.get("description", "")[:60]
            )
        ctx.console.print(Panel(table, title="[bold cyan]Registered Agents[/bold cyan]", border_style="cyan"))
    except Exception as e:
        ctx.console.print(f"[bold red]Failed to parse agents:[/bold red] {e}")


@registry.register("tokens", "Show token usage for this session")
def cmd_tokens_display(ctx: CommandContext, args: List[str]) -> None:
    i = ctx.state.total_input_tokens
    o = ctx.state.total_output_tokens
    ctx.console.print(Panel(
        f"[bold]Input tokens:[/bold]  {i:,}\\n"
        f"[bold]Output tokens:[/bold] {o:,}\\n"
        f"[bold]Total:[/bold]         {i+o:,}",
        title="[bold cyan]Token Usage[/bold cyan]",
        border_style="cyan"
    ))

'''

src = src.replace(
    'if __name__ == "__main__":',
    clear_cmd + '\nif __name__ == "__main__":'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('All fixes applied')
