from typing import List
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE
from organism_console.command_registry import registry, CommandContext
from organism_console.ui.live_stream import _AGENT_PERF

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
        from runtime_v2.services import model_registry as _reg
        _reg._AGENT_MODELS[agent_id] = (clean_model_name, backend)
        cmd_ctx.console.print(f"[bold green]✓ LIVE OVERRIDE ACTIVE[/bold green] | {agent_id.upper()} → [cyan]{clean_model_name}[/cyan] [dim]({backend})[/dim]")
        if hasattr(cmd_ctx.state, "reset_router"):
            cmd_ctx.state.reset_router()
    else:
        cmd_ctx.console.print("[bold red]✗ Failed to sync override to backend.[/bold red]")

@registry.register("status", "Show full system status including fallback pool and agent models")
def cmd_status(cmd_ctx: CommandContext, args: List[str]) -> None:
    from runtime_v2.services import model_registry as _reg

    resp = cmd_ctx.call_api("/status", "GET")
    table = Table(box=SIMPLE, header_style="bold cyan", show_header=True)
    table.add_column("Component", style="bold #555555", justify="right")
    table.add_column("Status")

    for agent_id, (model_name, backend) in _reg._AGENT_MODELS.items():
        color = "#00aaff" if backend == "ollama" else "#ffaa00" if backend == "groq" else "#ff00ff"
        table.add_row(agent_id.upper(), f"[{color}]{model_name}[/{color}] [dim]({backend})[/dim]")

    if resp and resp.status_code == 200:
        data = resp.json()
        fb = data.get("fallback_pool", {})
        total = fb.get("total", 0)
        groq_n = fb.get("groq", 0)
        orr_n = fb.get("openrouter", 0)
        table.add_row("FALLBACKS", f"[bold #00ffcc]{total} models ready[/bold #00ffcc] [dim](Groq: {groq_n}, OpenRouter: {orr_n})[/dim]")
    else:
        table.add_row("FALLBACKS", "[dim]Backend offline[/dim]")

    c_toks = cmd_ctx.state.cloud_input_tokens + cmd_ctx.state.cloud_output_tokens
    quota = cmd_ctx.state.cloud_token_quota
    q_pct = min(100, int((c_toks / quota) * 100)) if quota > 0 else 0
    cloud_state = "[bold green]ON[/bold green]" if cmd_ctx.state.cloud_enabled else "[bold red]OFF[/bold red]"
    table.add_row("CLOUD", f"{cloud_state} | Quota used: [#ffaa00]{q_pct}%[/#ffaa00] ({c_toks:,}/{quota:,} tokens)")

    cmd_ctx.console.print(Panel(table, title="[bold cyan]ZENITH System Status[/bold cyan]", border_style="cyan"))

@registry.register("cloud", "Toggle cloud model access on/off")
def cmd_cloud_toggle(cmd_ctx: CommandContext, args: List[str]) -> None:
    cmd_ctx.state.cloud_enabled = not cmd_ctx.state.cloud_enabled
    cmd_ctx.state.save()
    cmd_ctx.state.reset_router()
    state = "[bold green]ON[/bold green]" if cmd_ctx.state.cloud_enabled else "[bold red]OFF[/bold red]"
    cmd_ctx.console.print(f"[bold]Cloud access:[/bold] {state}")

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
