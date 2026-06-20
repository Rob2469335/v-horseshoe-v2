path = r'C:\Users\rober\Projects\v-horseshoe-v2\organism_console\command_registry.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

new_commands = '''

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
        table.add_column("Description", style="white")
        for a in agents:
            table.add_row(
                a.get("id", "?"),
                a.get("role", "?"),
                a.get("description", "")[:60]
            )
        ctx.console.print(Panel(table, title="[bold cyan]Registered Agents[/bold cyan]", border_style="cyan"))
    except Exception as e:
        ctx.console.print(f"[bold red]Failed to parse agents:[/bold red] {e}")

'''

# Insert before the exit command
src = src.replace(
    '@registry.register("exit", "Exit the terminal session"',
    new_commands + '@registry.register("exit", "Exit the terminal session"'
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
