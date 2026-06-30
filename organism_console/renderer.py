# organism_console/renderer.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.box import SIMPLE
from rich.markup import escape


def render_delegation_tree(chain: List[str]) -> str:
    """Renders the agent delegation chain as an ASCII tree."""
    if not chain:
        return "[dim](no active delegation chain)[/dim]"
    
    lines = []
    for i, agent in enumerate(chain):
        if i == 0:
            lines.append(f"[bold green]{agent}[/bold green]")
        elif i == 1:
            lines.append(f" ├── [cyan]{agent}[/cyan]")
        else:
            indent = " │" + "      " * (i - 2) + "     "
            lines.append(f"{indent}└── [cyan]{agent}[/cyan]")
    return "\n".join(lines)


def render_step_micro_ui(phase: str, message: str) -> str:
    """Formats raw step updates into styled micro-UI lines using emojis."""
    emoji_map = {
        "thinking": "🧠",
        "planning": "📋",
        "sensing": "📡",
        "repair": "🩹",
        "swarm": "🐝",
        "executing": "⚙️",
        "tool_call": "🔧",
        "model_selected": "🚀",
        "escalation": "⚠️",
    }
    emoji = emoji_map.get(phase.lower(), "⚙️")
    return f"{emoji} [bold bright_white]{phase.capitalize()}[/bold bright_white] → {escape(message)}"


def render_trace_panel(
    title: str,
    details: Dict[str, Any],
    border_style: str = "cyan"
) -> Panel:
    """Formats execution trace data inside structured and styled panels."""
    table = Table(show_header=False, box=SIMPLE, padding=(0, 1))
    table.add_column("Key", style="bold yellow")
    table.add_column("Value", style="white")

    for k, v in details.items():
        # Handle complex types gracefully
        if isinstance(v, (dict, list)):
            import json
            val_str = json.dumps(v, default=str)
        else:
            val_str = str(v)
        table.add_row(k.replace("_", " ").title(), escape(val_str))

    return Panel(
        table,
        title=f"[bold bright_white]{title}[/bold bright_white]",
        border_style=border_style,
        expand=False
    )


def render_dashboard(
    state: Any,
    system_stats: Dict[str, Any],
    backend_ok: bool,
    ollama_ok: bool,
    installed_models: List[str],
    available_tools: str = "index_codebase, vscode_automation, chat_search"
) -> Panel:
    """Builds a comprehensive status dashboard view."""
    # Main outer table splits the dashboard into columns
    main_table = Table.grid(padding=(0, 2))
    main_table.add_column("Left", width=40)
    main_table.add_column("Right", width=50)

    # 1. System Health column (Left)
    health_table = Table(show_header=False, box=SIMPLE)
    health_table.add_row("Backend Server", "[green]ONLINE[/green]" if backend_ok else "[red]OFFLINE[/red]")
    health_table.add_row("Ollama API", "[green]ONLINE[/green]" if ollama_ok else "[red]OFFLINE[/red]")
    health_table.add_row("CPU Load", f"{system_stats['cpu']:.1f}%")
    health_table.add_row(
        "RAM Usage",
        f"[{system_stats['ram_color']}]{system_stats['ram_pct']:.1f}%[/{system_stats['ram_color']}] ({system_stats['ram_used_gb']:.1f}/{system_stats['ram_total_gb']:.1f}GB)"
    )
    health_table.add_row("Trace Mode", "[green]ON[/green]" if state.trace_mode else "[yellow]OFF[/yellow]")
    health_table.add_row("CLI Mode", f"[bold cyan]{state.mode.upper()}[/bold cyan]")
    health_panel = Panel(health_table, title="[bold cyan]System Health & Context[/bold cyan]", border_style="cyan")

    # 2. Active Agent / State column (Right)
    state_table = Table(show_header=False, box=SIMPLE)
    state_table.add_row("Active Agent", f"[bold cyan]{state.active_agent}[/bold cyan]")
    state_table.add_row("Active Model", f"[bold green]{state.active_model}[/bold green]")
    state_table.add_row("Cloud Fallback", "[green]Enabled[/green]" if state.cloud_enabled else "[yellow]Disabled[/yellow]")
    state_table.add_row("Current Topic", escape(state.current_topic))
    state_table.add_row("Strategic Intent", escape(state.strategic_intent or "(none)"))
    
    # Render delegation chain tree as part of the state panel
    tree_str = render_delegation_tree(state.delegation_chain)
    state_table.add_row("Delegation Path", tree_str)
    
    state_panel = Panel(state_table, title="[bold green]Agent State[/bold green]", border_style="green")

    main_table.add_row(health_panel, state_panel)

    # 3. Running/Installed Models sub-section
    models_str = ", ".join(installed_models[:10]) + ("..." if len(installed_models) > 10 else "")
    if not models_str:
        models_str = "[dim](no models installed)[/dim]"
    
    summary_table = Table(show_header=False, box=SIMPLE)
    summary_table.add_row("Installed Models", models_str)
    summary_table.add_row("Available Tools", available_tools)
    summary_panel = Panel(summary_table, title="[bold yellow]Capabilities[/bold yellow]", border_style="yellow")

    # Stack the columns and capabilities panel
    layout_table = Table.grid()
    layout_table.add_row(main_table)
    layout_table.add_row(summary_panel)

    return Panel(
        layout_table,
        title="[bold bright_white]🤖 Zenith Swarm Control Dashboard[/bold bright_white]",
        border_style="blue",
        expand=False
    )
