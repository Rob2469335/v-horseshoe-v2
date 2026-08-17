"""System-level CLI commands for the organism console."""

import json
import os
import re
from pathlib import Path
from typing import List

from rich.panel import Panel
from rich.table import Table
from rich.box import SIMPLE
from rich.markup import escape

from organism_console.command_registry import registry
from organism_console._command_context import CommandContext


@registry.register("help", "Show available control commands", aliases=["?"])
def cmd_help(ctx: CommandContext, args: List[str]) -> None:
    table = Table(box=SIMPLE, header_style="bold cyan", border_style="blue")
    table.add_column("Command", style="bold green", no_wrap=True)
    table.add_column("Description", style="white")
    unique_cmds = {}
    for k, v in registry.commands.items():
        if v["func"] not in unique_cmds.values():
            unique_cmds[k] = v
    for name in sorted(unique_cmds.keys()):
        cmd = unique_cmds[name]
        table.add_row(f"/{name}", cmd["description"])
    ctx.console.print(
        Panel(
            table,
            title="[bold cyan]Swarm OS Control Commands[/bold cyan]",
            border_style="blue",
        )
    )


@registry.register("model", "Set active model. Usage: /model set <model_name>")
def cmd_model(ctx: CommandContext, args: List[str]) -> None:
    if not args or args[0].lower() != "set":
        ctx.console.print(
            f"Active model: [bold green]{ctx.state.active_model}[/bold green]"
        )
        ctx.console.print("To change: `/model set <model_name>`")
        return
    if len(args) < 2:
        ctx.console.print(
            "[yellow]Error: Specify a model name. Example: `/model set qwen3.5-4b`[/yellow]"
        )
        return
    model_name = args[1]
    ctx.state.active_model = model_name
    ctx.state.save()
    ctx.console.print(
        f"[green]✓ Active model set to[/green] [bold green]{model_name}[/bold green]"
    )


@registry.register("picker", "Launch the interactive model picker TUI")
def cmd_picker(ctx: CommandContext, args: List[str]) -> None:
    try:
        from organism_console.ui.picker import launch_picker
    except ImportError:
        ctx.console.print(
            "[yellow]Model picker TUI requires 'textual' (pip install textual). "
            "Falling back: use /model set <name> or /model to view.[/yellow]"
        )
        return
    launch_picker(ctx)


@registry.register(
    "agent", "Switch the active agent. Usage: /agent <name> (or /agent list)"
)
def cmd_agent(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        resp = ctx.call_api("/agents", "GET")
        agent_list = "?"
        if resp:
            try:
                names = [a.get("id", "?") for a in resp.json()]
                agent_list = ", ".join(names)
            except Exception:
                pass
        ctx.console.print(
            f"Active agent: [bold cyan]{ctx.state.active_agent}[/bold cyan]"
        )
        ctx.console.print(f"Available: {agent_list}")
        return
    agent_name = args[0].lower()
    ctx.state.active_agent = agent_name
    ctx.state.delegation_chain = [agent_name]
    ctx.state.save()
    ctx.console.print(
        f"[green]✓ Switched active agent to[/green] [bold cyan]{agent_name}[/bold cyan]"
    )


@registry.register("status", "Check system and service health")
def cmd_status(ctx: CommandContext, args: List[str]) -> None:
    resp = ctx.call_api("/readyz", "GET")
    if not resp:
        ctx.console.print("[bold red]✗ Backend offline[/bold red]")
        return
    d = resp.json()
    ready = d.get("ready", False)
    health_color = "green" if ready else "red"
    table = Table(show_header=False, box=SIMPLE)
    table.add_row(
        "Swarm Status",
        f"[{health_color}]{d.get('status', 'unknown').upper()}[/{health_color}]",
    )
    table.add_row("Health Score", f"{d.get('health_score', 0)}/100")
    checks = d.get("checks", {})
    for check_name, passed in checks.items():
        status_symbol = "[green]✓[/green]" if passed else "[red]✗[/red]"
        table.add_row(f"  {check_name.replace('_', ' ').title()}", status_symbol)
    # 2026 autonomy rollback Phase B: surface any human-review flags from the
    # canary system where a human will actually see them — not just in the audit
    # trail. A logged flag that sits unread is the 'dead but silent' daemon
    # problem one layer up.
    try:
        from pathlib import Path as _P

        f = _P("data/events/human_review.jsonl")
        if f.exists():
            pending = [
                l
                for l in f.read_text(encoding="utf-8", errors="ignore").splitlines()
                if l.strip()
            ]
            if pending:
                table.add_row(
                    "Pending Human Review",
                    f"[bold yellow]{len(pending)} flag(s) — check data/events/human_review.jsonl or /heal[/bold yellow]",
                )
    except Exception:
        pass
    ctx.console.print(
        Panel(table, title="[bold cyan]System Status[/bold cyan]", border_style="cyan")
    )


@registry.register(
    "trace", "Configure trace mode or export trace. Usage: /trace on|off|export"
)
def cmd_trace(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            f"Trace mode is currently [bold]{'ON' if ctx.state.trace_mode else 'OFF'}[/bold]"
        )
        return
    arg = args[0].lower()
    if arg == "on":
        ctx.state.trace_mode = True
        ctx.console.print("[green]✓ Trace mode enabled.[/green]")
        ctx.state.save()
    elif arg == "off":
        ctx.state.trace_mode = False
        ctx.console.print("[yellow]! Trace mode disabled.[/yellow]")
        ctx.state.save()
    elif arg == "export":
        from datetime import datetime

        export_dir = Path(__file__).parent.parent / "swarm_os" / "logs"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            export_dir / f"trace_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        try:
            lines = [
                "# Swarm OS Control Terminal Trace Export",
                f"Generated at: {datetime.now().isoformat()}",
                f"Active Agent: {ctx.state.active_agent}",
                f"Active Model: {ctx.state.active_model}",
                "\n---\n",
            ]
            for idx, run in enumerate(ctx.state.history):
                role = run.get("role", "unknown")
                content = run.get("content", "")
                lines.extend(
                    [
                        f"## Run #{idx} ({role})",
                        "\n### Content",
                        f"```\n{content}\n```",
                        "\n---\n",
                    ]
                )
            filename.write_text("\n".join(lines), encoding="utf-8")
            ctx.console.print(
                f"[green]✓ Trace log successfully exported to [bold]{filename}[/bold][/green]"
            )
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to export trace log:[/bold red] {e}")
    else:
        ctx.console.print("[yellow]Usage: /trace on|off|export[/yellow]")


@registry.register("cloud", "Toggle cloud models on or off. Usage: /cloud [on|off]")
def cmd_cloud(ctx: CommandContext, args: List[str]) -> None:
    if args:
        arg = args[0].lower()
        if arg == "on":
            ctx.state.cloud_enabled = True
        elif arg == "off":
            ctx.state.cloud_enabled = False
        else:
            ctx.console.print("[yellow]Usage: /cloud [on|off][/yellow]")
            return
    else:
        ctx.state.cloud_enabled = not ctx.state.cloud_enabled
    ctx.state.save()
    if ctx.state.cloud_enabled:
        os.environ["CLOUD_MODEL_ALLOWLIST"] = "free"
    status = (
        "[bold green]ON[/bold green]"
        if ctx.state.cloud_enabled
        else "[bold red]OFF[/bold red]"
    )
    ctx.console.print(
        f"Cloud models are now {status} [cyan](All free OpenRouter/Groq/NVIDIA/Gemini models enabled)[/cyan]."
    )


@registry.register("quota", "Set daily cloud token quota (e.g. /quota 100000)")
def cmd_quota(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            f"Current cloud quota: {ctx.state.cloud_token_quota:,} tokens"
        )
        return
    try:
        quota = int(args[0])
        ctx.state.cloud_token_quota = quota
        ctx.state.save()
        ctx.console.print(
            f"Cloud token quota set to [bold cyan]{quota:,}[/bold cyan] tokens."
        )
    except ValueError:
        ctx.console.print(
            "[bold red]Invalid quota amount.[/bold red] Usage: /quota 100000"
        )


@registry.register("tokens", "Show estimated token counts and session cost")
def cmd_tokens(ctx: CommandContext, args: List[str]) -> None:
    input_tokens = ctx.state.total_input_tokens
    output_tokens = ctx.state.total_output_tokens
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Type", style="bold yellow")
    table.add_column("Count", style="white")
    table.add_row("Input Tokens", f"{input_tokens:,}")
    table.add_row("Output Tokens", f"{output_tokens:,}")
    table.add_row("Total Session", f"{input_tokens + output_tokens:,}")
    ctx.console.print(
        Panel(
            table,
            title="[bold cyan]Token & Cost Tracking[/bold cyan]",
            border_style="cyan",
        )
    )
    # Real persisted cost from usage_log (per-model pricing), not hardcoded
    # rates. Falls back to a note when no usage data has been recorded yet.
    try:
        from runtime_v2.services.usage_log import usage_report

        report = usage_report(days=30)
    except Exception:
        report = None
    if report and report.get("rows"):
        cost_table = Table(box=SIMPLE, header_style="bold cyan")
        cost_table.add_column("Model", style="bold yellow")
        cost_table.add_column("Calls", style="white")
        cost_table.add_column("Cost ($)", style="green")
        for model, m in sorted(report.get("per_model", {}).items()):
            cost_table.add_row(
                model, str(m.get("calls", 0)), f"${m.get('cost') or 0:.4f}"
            )
        cost_table.add_row(
            "[bold]Total (known)[/bold]",
            str(report.get("rows", 0)),
            f"${report.get('known_cost') or 0:.4f}",
        )
        ctx.console.print(
            Panel(
                cost_table,
                title="[bold green]Real Usage Cost (30d)[/bold green]",
                border_style="green",
            )
        )
        if report.get("unknown_cost"):
            ctx.console.print(
                f"[dim]Plus ${report['unknown_cost']:.4f} of unmetered traffic (models without a price table entry).[/dim]"
            )
    else:
        ctx.console.print(
            "[dim]No persisted usage data yet — cost appears here once LLM calls are recorded in data/usage/usage.jsonl.[/dim]"
        )
    from organism_console.token_tracker import get_status_segment

    tracker_status = get_status_segment()
    if tracker_status:
        ctx.console.print(
            Panel(
                tracker_status,
                title="[bold magenta]Live Provider Breakdown[/bold magenta]",
                border_style="magenta",
            )
        )


@registry.register("tracker", "Show live token tracker and provider status")
def cmd_tracker(ctx: CommandContext, args: List[str]) -> None:
    from organism_console.token_tracker import get_status_segment

    status = get_status_segment()
    if status:
        ctx.console.print(
            Panel(
                status,
                title="[bold cyan]Live Token Tracker[/bold cyan]",
                border_style="cyan",
            )
        )
    else:
        ctx.console.print("[dim]Tracker not available.[/dim]")


@registry.register("mode", "Set console mode. Usage: /mode safe|dev")
def cmd_mode(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            f"Current mode: [bold cyan]{ctx.state.mode.upper()}[/bold cyan]"
        )
        return
    arg = args[0].lower()
    if arg in ("safe", "dev"):
        ctx.state.mode = arg
        ctx.state.save()
        ctx.console.print(
            f"[green]✓ Mode switched to[/green] [bold cyan]{arg.upper()}[/bold cyan]"
        )
    else:
        ctx.console.print("[yellow]Usage: /mode safe|dev[/yellow]")


@registry.register("dashboard", "Show the system dashboard widget")
def cmd_dashboard(ctx: CommandContext, args: List[str]) -> None:
    from organism_console.renderer import render_dashboard

    system_stats = ctx.get_system_stats()
    resp = ctx.call_api("/readyz", "GET")
    backend_ok = resp is not None
    ollama_ok = False
    if backend_ok:
        try:
            ollama_ok = resp.json().get("checks", {}).get(
                "llamacpp_reachable", False
            ) or resp.json().get("checks", {}).get("ollama_reachable", False)
        except Exception:
            pass
    tools_resp = ctx.call_api("/tools", "GET")
    tools_str = "None"
    if tools_resp:
        try:
            tools_list = tools_resp.json().get("capabilities", [])
            tools_str = ", ".join(tools_list[:6])
            if len(tools_list) > 6:
                tools_str += "..."
        except Exception:
            pass
    dashboard_panel = render_dashboard(
        state=ctx.state,
        system_stats=system_stats,
        backend_ok=backend_ok,
        ollama_ok=ollama_ok,
        installed_models=ctx.installed_models,
        available_tools=tools_str,
    )
    ctx.console.print(dashboard_panel)


@registry.register(
    "tools",
    "List available agent tools, or dynamically create one. Usage: /tools [create <name>]",
)
def cmd_tools(ctx: CommandContext, args: List[str]) -> None:
    if args and args[0].lower() == "create":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /tools create <name>[/yellow]")
            return
        tool_name = args[1].lower().strip()
        if not re.match(r"^[a-z0-9_]+$", tool_name):
            ctx.console.print(
                "[bold red]Error: Tool name must be alphanumeric with underscores only.[/bold red]"
            )
            return
        from rich.prompt import Prompt, Confirm

        description = Prompt.ask("Describe what this capability should do")
        if not description.strip():
            ctx.console.print(
                "[yellow]Cancelled: Description cannot be empty.[/yellow]"
            )
            return
        ctx.console.print(
            f"[bold cyan]Synthesizing capability [green]{tool_name}[/green]...[/bold cyan]"
        )
        class_name = (
            "".join(part.capitalize() for part in tool_name.split("_")) + "Handler"
        )
        prompt = f"""
        Generate a Python class named {class_name} conforming to the Swarm OS capability pattern.
        The class must have an async `execute(self, payload: Any) -> Dict[str, Any]` method.
        Requirements for the tool:
        {description}
        Return ONLY valid python code inside a single ```python ``` codeblock.
        """
        model = ctx.state.active_model or "qwen3.5-4b"
        code = ""
        try:
            resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
            if resp and resp.status_code == 200:
                resp_text = resp.json().get("response", "")
                m = re.search(r"```python\s*(.*?)\s*```", resp_text, re.DOTALL)
                code = m.group(1).strip() if m else resp_text.strip()
            else:
                ctx.console.print(
                    "[bold red]Error: Failed to contact generator model.[/bold red]"
                )
                return
        except Exception as e:
            ctx.console.print(f"[bold red]Error calling generator: {e}[/bold red]")
            return
        if not code:
            ctx.console.print("[bold red]Error: Generated code is empty.[/bold red]")
            return
        import ast as _ast

        try:
            _ast.parse(code)
        except SyntaxError as _se:
            ctx.console.print(
                f"[bold red]Generated code has a syntax error and was rejected: {_se}[/bold red]"
            )
            return
        _BANNED_PATTERNS = [
            "__import__",
            "importlib.import_module",
            "subprocess",
            "os.system",
            "os.popen",
            "eval(",
            "exec(",
            "compile(",
            "socket.",
            "ctypes",
        ]
        _code_check = code.lower()
        _hits = [b for b in _BANNED_PATTERNS if b in _code_check]
        if _hits:
            ctx.console.print(
                f"[bold red]Generated code uses banned patterns ({', '.join(_hits)}) and was rejected.[/bold red]"
            )
            return
        _tree = _ast.parse(code)
        _has_execute = any(
            isinstance(node, _ast.AsyncFunctionDef) and node.name == "execute"
            for node in _ast.walk(_tree)
        )
        if not _has_execute:
            ctx.console.print(
                "[bold red]Generated code does not define 'async def execute()' and was rejected.[/bold red]"
            )
            return
        ctx.console.print(
            Panel(
                escape(code),
                title=f"Generated Code for {tool_name}.py",
                border_style="cyan",
            )
        )
        if not Confirm.ask(
            "Do you want to save this capability to the sandbox for review?"
        ):
            ctx.console.print("[yellow]Tool creation cancelled by user.[/yellow]")
            return
        capabilities_dir = Path(__file__).parent.parent / "swarm_os" / "sandbox_tools"
        file_path = capabilities_dir / f"{tool_name}.py"
        try:
            capabilities_dir.mkdir(parents=True, exist_ok=True)
            ctx.console.print(
                "[yellow]Generated code saved to sandbox_tools/. Please manually verify it before moving it into capabilities/.[/yellow]"
            )
            file_path.write_text(code, encoding="utf-8")
            ctx.console.print(
                f"[green]✓ Successfully wrote capability to [bold]{file_path}[/bold][/green]"
            )
        except Exception as e:
            ctx.console.print(
                f"[bold red]Failed to write capability file: {e}[/bold red]"
            )
        return
    resp = ctx.call_api("/tools", "GET")
    if not resp:
        ctx.console.print("[bold red]✗ Backend offline[/bold red]")
        return
    d = resp.json()
    capabilities = d.get("capabilities", [])
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Exposed Tools", style="bold white")
    for cap in capabilities:
        table.add_row(cap)
    ctx.console.print(
        Panel(
            table,
            title=f"[bold cyan]Tool Capabilities ({d.get('count', 0)})[/bold cyan]",
            border_style="cyan",
        )
    )


@registry.register(
    "mcp",
    "Manage registered MCP tool servers. Usage: /mcp list | /mcp prune <server_name>",
)
def cmd_mcp(ctx: CommandContext, args: List[str]) -> None:
    config_path = Path(__file__).parent.parent / "swarm_config.json"
    if not config_path.exists():
        ctx.console.print("[red]swarm_config.json not found.[/red]")
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        ctx.console.print(f"[red]Failed to read swarm_config.json: {e}[/red]")
        return
    servers = config.get("mcp_servers", {})
    if not args or args[0].lower() == "list":
        t = Table(header_style="bold cyan")
        t.add_column("Server", style="bold white")
        t.add_column("Command")
        t.add_column("Args")
        if not servers:
            ctx.console.print("[yellow]No MCP servers registered.[/yellow]")
            return
        for name, cfg in servers.items():
            t.add_row(name, cfg.get("command", ""), " ".join(cfg.get("args", [])))
        ctx.console.print(
            Panel(
                t,
                title="[bold cyan]Registered MCP Servers[/bold cyan]",
                border_style="cyan",
            )
        )
        return
    if args[0].lower() == "prune" and len(args) >= 2:
        name = args[1]
        if name not in servers:
            ctx.console.print(
                f"[red]Server '{name}' not found. Use /mcp list to see registered servers.[/red]"
            )
            return
        from rich.prompt import Confirm

        if Confirm.ask(
            f"[bold yellow]Remove MCP server '{name}' from swarm_config.json?[/bold yellow]"
        ):
            del config["mcp_servers"][name]
            try:
                config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
                ctx.console.print(
                    f"[green]✓ Removed '{name}'. Restart the backend to take effect.[/green]"
                )
            except Exception as e:
                ctx.console.print(f"[red]Failed to write config: {e}[/red]")
        return
    ctx.console.print("[yellow]Usage: /mcp list | /mcp prune <server_name>[/yellow]")


@registry.register(
    "prompt",
    "View or override system mandates for an agent. Usage: /prompt <agent_id> [new_mandate]",
)
def cmd_prompt(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            "[yellow]Usage: /prompt <agent_id> [new_mandate]. Example: /prompt coder Be very brief.[/yellow]"
        )
        return
    agent_id = args[0].lower()
    project_root = Path(__file__).parent.parent.resolve()
    mandates_file = project_root / "docs" / "agent_mandates.json"
    custom_mandates = {}
    if mandates_file.exists():
        try:
            custom_mandates = json.loads(mandates_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if len(args) < 2:
        if agent_id in custom_mandates:
            ctx.console.print(
                Panel(
                    custom_mandates[agent_id],
                    title=f"Custom Prompt Mandate for '{agent_id}'",
                    border_style="green",
                )
            )
        else:
            ctx.console.print(
                f"[dim]Agent '{agent_id}' has no custom override. It uses the default role mandate.[/dim]"
            )
        return
    new_mandate = " ".join(args[1:])
    custom_mandates[agent_id] = new_mandate
    try:
        mandates_file.parent.mkdir(parents=True, exist_ok=True)
        mandates_file.write_text(
            json.dumps(custom_mandates, indent=2), encoding="utf-8"
        )
        ctx.console.print(
            f"[bold green]✓ Custom system prompt mandate for '{agent_id}' successfully updated![/bold green]"
        )
    except Exception as e:
        ctx.console.print(
            f"[bold red]Error saving prompt mandate override: {e}[/bold red]"
        )


@registry.register("routing", "Show current routing mode.")
def cmd_routing(ctx: CommandContext, args: List[str]) -> None:
    mode = os.environ.get("SWARM_ROUTING_MODE", "auto")
    ctx.console.print(f"[bold magenta]Routing mode:[/bold magenta] {mode}")


@registry.register("local", "Force local-only routing for model fallbacks.")
def cmd_local(ctx: CommandContext, args: List[str]) -> None:
    os.environ["SWARM_ROUTING_MODE"] = "local_only"
    ctx.console.print("[bold green]Routing mode:[/bold green] local_only")


@registry.register("auto", "Use local-first routing, then cloud fallbacks if needed.")
def cmd_auto(ctx: CommandContext, args: List[str]) -> None:
    os.environ["SWARM_ROUTING_MODE"] = "auto"
    ctx.console.print("[bold cyan]Routing mode:[/bold cyan] auto")


@registry.register("cloud-on", "Allow cloud escalation in fallback routing.")
def cmd_cloud_on(ctx: CommandContext, args: List[str]) -> None:
    os.environ["SWARM_ROUTING_MODE"] = "cloud_allowed"
    ctx.console.print("[bold yellow]Routing mode:[/bold yellow] cloud_allowed")


@registry.register("cloud-off", "Disable cloud fallback routing and stay local-only.")
def cmd_cloud_off(ctx: CommandContext, args: List[str]) -> None:
    os.environ["SWARM_ROUTING_MODE"] = "local_only"
    ctx.console.print("[bold green]Routing mode:[/bold green] local_only")


@registry.register(
    "cooldowns",
    "Inspect or clear model fallback cooldowns. Usage: /cooldowns "
    "[list | clear <model>] — clear is scoped to ONE model (the "
    "manual exit for a billing-402 pin after a top-up).",
)
def cmd_cooldowns(ctx: CommandContext, args: List[str]) -> None:
    from runtime_v2.services.fallback_manager import (
        _cooldowns,
        clear_model_cooldown,
    )

    sub = (args[0] if args else "").lower()
    if sub == "clear":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /cooldowns clear <model-id>[/yellow]")
            return
        cleared = clear_model_cooldown(args[1])
        if cleared:
            ctx.console.print(
                f"[bold green]Cleared cooldown[/bold green] for [cyan]{args[1]}[/cyan]"
            )
        else:
            ctx.console.print(f"[dim]No cooldown entry for {args[1]}[/dim]")
        return
    # list (default)
    with __import__(
        "runtime_v2.services.fallback_manager", fromlist=["_cooldowns_lock_sync"]
    )._cooldowns_lock_sync():
        entries = dict(_cooldowns)
    if not entries:
        ctx.console.print("[dim]No models in cooldown.[/dim]")
        return
    for mid, e in entries.items():
        until = e.get("until", 0)
        label = (
            "PERMANENT (billing/auth pin — clear after top-up via /cooldowns clear)"
            if until == float("inf")
            else f"until {until:.0f}"
        )
        ctx.console.print(
            f"[cyan]{mid}[/cyan]: {label} ({e.get('failures', 0)} failure(s))"
        )


@registry.register("speak", "Toggle speech feedback")
def cmd_speak(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.speech_enabled = not getattr(ctx.state, "speech_enabled", False)
    ctx.state.save()
    status = (
        "[bold green]ON[/bold green]"
        if ctx.state.speech_enabled
        else "[bold red]OFF[/bold red]"
    )
    ctx.console.print(f"Speech feedback is now {status}.")
