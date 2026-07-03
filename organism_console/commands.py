from typing import List
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE
from organism_console.command_registry import registry, CommandContext
from organism_console.ui.live_stream import _AGENT_PERF


@registry.register("picker", "Launch interactive interactive model picker", aliases=["models", "model"])
def cmd_model(cmd_ctx: CommandContext, args: List[str]) -> None:
    from organism_console.ui.picker import launch_picker, push_model_override, parse_backend

    if not args:
        launch_picker(cmd_ctx)
        return

    agent_id = args[0].lower()
    model_name = args[1] if len(args) > 1 else ""
    
    if not model_name:
        cmd_ctx.console.print("[yellow]Usage: /model <agent_id> <model_name>[/yellow]")
        cmd_ctx.console.print("[dim]Or just run /model with no arguments for the interactive picker.[/dim]")
        return

    backend, clean_model_name = parse_backend(model_name)
    
    cmd_ctx.console.print(f"[dim]Syncing {agent_id} model override to backend...[/dim]")
    success = push_model_override(agent_id, clean_model_name, backend)
    if success:
        cmd_ctx.console.print(f"[bold green]✓ LIVE OVERRIDE ACTIVE[/bold green] | {agent_id.upper()} → [cyan]{clean_model_name}[/cyan] [dim]({backend})[/dim]")
        if hasattr(cmd_ctx.state, "reset_router"):
            cmd_ctx.state.reset_router()
    else:
        cmd_ctx.console.print("[bold red]✗ Failed to sync override to backend.[/bold red]")

@registry.register("perf", "Show per-agent response time performance metrics")
def cmd_perf(cmd_ctx: CommandContext, args: List[str]) -> None:
    if not _AGENT_PERF:
        cmd_ctx.console.print("[dim]No agent calls recorded yet in this session.[/dim]")
        return

    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Agent", style="bold")
    table.add_column("Calls", justify="right")
    table.add_column("Avg Time", justify="right")
    table.add_column("Last Time", justify="right")
    table.add_column("Status")

    for agent_id, perf in sorted(_AGENT_PERF.items()):
        avg = perf["total"] / perf["count"] if perf["count"] > 0 else 0.0
        last = perf["last"]
        status = "[bold red]SLOW[/bold red]" if avg > 60 else "[bold yellow]OK[/bold yellow]" if avg > 15 else "[bold green]FAST[/bold green]"
        table.add_row(
            agent_id,
            str(perf["count"]),
            f"{avg:.1f}s",
            f"{last:.1f}s",
            status
        )

    cmd_ctx.console.print(Panel(table, title="[bold cyan]Agent Performance[/bold cyan]", border_style="cyan"))

