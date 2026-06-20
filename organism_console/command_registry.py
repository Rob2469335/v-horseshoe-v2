# organism_console/command_registry.py
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import ast
import re
import concurrent.futures
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE

from organism_console.renderer import render_dashboard



class CommandContext:
    def __init__(
        self,
        state: Any,
        console: Console,
        call_api: Callable[[str, str, Optional[Any], bool], Any],
        run_prompt: Callable[[str], None],
        get_system_stats: Callable[[], Dict[str, Any]],
        installed_models: List[str],
        run_goal_loop: Optional[Callable[[str], None]] = None,
        run_debate: Optional[Callable[[str], None]] = None
    ) -> None:
        self.state = state
        self.console = console
        self.call_api = call_api
        self.run_prompt = run_prompt
        self.get_system_stats = get_system_stats
        self.installed_models = installed_models
        self.run_goal_loop = run_goal_loop
        self.run_debate = run_debate


class CommandRegistry:
    def __init__(self) -> None:
        self.commands: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, aliases: Optional[List[str]] = None) -> Callable:
        def decorator(func: Callable) -> Callable:
            cmd_info = {
                "func": func,
                "description": description,
                "aliases": aliases or []
            }
            self.commands[name] = cmd_info
            for alias in cmd_info["aliases"]:
                self.commands[alias] = cmd_info
            return func
        return decorator

    def handle_line(self, line: str, ctx: CommandContext) -> Optional[str]:
        """
        Parses and executes a command if it starts with '/'.
        Returns a prompt string to execute if `/replay` was triggered.
        """
        raw = line.strip()
        if not raw:
            return None
        
        if not raw.startswith("/"):
            # Not a command, pass it back to REPL as a prompt
            return raw

        parts = raw.split()
        cmd_name = parts[0][1:].lower()  # Strip '/'
        args = parts[1:]

        # Record entered command to command history
        ctx.state.command_history.append(raw)
        ctx.state.save()

        if cmd_name not in self.commands:
            ctx.console.print(f"[bold red]Unknown command:[/bold red] /{cmd_name}. Type `/help` to list commands.")
            return None

        cmd_info = self.commands[cmd_name]
        try:
            # Command execution can return a prompt string for replaying
            return cmd_info["func"](ctx, args)
        except Exception as e:
            ctx.console.print(f"[bold red]Command failed:[/bold red] {e}")
            ctx.state.last_error = str(e)
            ctx.state.save()
            return None


registry = CommandRegistry()


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

    ctx.console.print(Panel(table, title="[bold cyan]Swarm OS Control Commands[/bold cyan]", border_style="blue"))


@registry.register("model", "Set active model. Usage: /model set <model_name>")
def cmd_model(ctx: CommandContext, args: List[str]) -> None:
    if not args or args[0].lower() != "set":
        ctx.console.print(f"Active model: [bold green]{ctx.state.active_model}[/bold green]")
        ctx.console.print("To change: `/model set <model_name>`")
        return

    if len(args) < 2:
        ctx.console.print("[yellow]Error: Specify a model name. Example: `/model set qwen2.5:7b`[/yellow]")
        return

    model_name = args[1]
    ctx.state.active_model = model_name
    ctx.state.save()
    ctx.console.print(f"[green]✓ Active model set to[/green] [bold green]{model_name}[/bold green]")


@registry.register("agent", "Switch the active agent. Usage: /agent <name>")
def cmd_agent(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(f"Active agent: [bold cyan]{ctx.state.active_agent}[/bold cyan]")
        ctx.console.print("To change: `/agent <name>` (coordinator, planner, executor, coder, reviewer)")
        return

    agent_name = args[0].lower()
    ctx.state.active_agent = agent_name
    ctx.state.delegation_chain = [agent_name]  # Reset chain to this root agent
    ctx.state.save()
    ctx.console.print(f"[green]✓ Switched active agent to[/green] [bold cyan]{agent_name}[/bold cyan]")


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
    table.add_row("Swarm Status", f"[{health_color}]{d.get('status', 'unknown').upper()}[/{health_color}]")
    table.add_row("Health Score", f"{d.get('health_score', 0)}/100")
    
    checks = d.get("checks", {})
    for check_name, passed in checks.items():
        status_symbol = "[green]✓[/green]" if passed else "[red]✗[/red]"
        table.add_row(f"  {check_name.replace('_', ' ').title()}", status_symbol)
        
    ctx.console.print(Panel(table, title="[bold cyan]System Status[/bold cyan]", border_style="cyan"))


@registry.register("trace", "Configure trace mode or export trace. Usage: /trace on|off|export")
def cmd_trace(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(f"Trace mode is currently [bold]{'ON' if ctx.state.trace_mode else 'OFF'}[/bold]")
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
        if not ctx.state.history:
            ctx.console.print("[yellow]No session history available to export.[/yellow]")
            return
        
        from datetime import datetime
        export_dir = Path("swarm_os/logs")
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = export_dir / f"trace_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        try:
            lines = [
                "# Swarm OS Control Terminal Trace Export",
                f"Generated at: {datetime.now().isoformat()}",
                f"Active Agent: {ctx.state.active_agent}",
                f"Active Model: {ctx.state.active_model}",
                "\n---\n"
            ]
            for idx, run in enumerate(ctx.state.history):
                lines.extend([
                    f"## Run #{idx} ({run.get('agent_id', 'unknown')})",
                    f"**Timestamp**: {datetime.fromtimestamp(run.get('timestamp', 0)).isoformat() if run.get('timestamp') else 'unknown'}",
                    "\n### User Prompt",
                    f"```\n{run.get('prompt', '')}\n```",
                    "\n### Assistant Response",
                    f"```\n{run.get('response', '')}\n```",
                    "\n---\n"
                ])
                
            filename.write_text("\n".join(lines), encoding="utf-8")
            ctx.console.print(f"[green]✓ Trace log successfully exported to [bold]{filename}[/bold][/green]")
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to export trace log:[/bold red] {e}")
    else:
        ctx.console.print("[yellow]Usage: /trace on|off|export[/yellow]")


@registry.register("tokens", "Show estimated token counts and session cost")
def cmd_tokens(ctx: CommandContext, args: List[str]) -> None:
    input_tokens = ctx.state.total_input_tokens
    output_tokens = ctx.state.total_output_tokens
    
    # Pricing: input $0.0015/1k, output $0.006/1k tokens
    input_cost = (input_tokens / 1000) * 0.0015
    output_cost = (output_tokens / 1000) * 0.0060
    total_cost = input_cost + output_cost
    
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Type", style="bold yellow")
    table.add_column("Count", style="white")
    table.add_column("Est. Cost ($)", style="green")
    
    table.add_row("Input Tokens", f"{input_tokens:,}", f"${input_cost:.5f}")
    table.add_row("Output Tokens", f"{output_tokens:,}", f"${output_cost:.5f}")
    table.add_row("Total Session", f"{input_tokens + output_tokens:,}", f"${total_cost:.5f}")
    
    ctx.console.print(Panel(table, title="[bold cyan]Token & Cost Tracking[/bold cyan]", border_style="cyan"))


@registry.register("diff", "Show Git diff of current changes in workspace")
def cmd_diff(ctx: CommandContext, args: List[str]) -> None:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.resolve()
        )
        diff_text = result.stdout
        if not diff_text.strip():
            ctx.console.print("[dim]No modifications in workspace git tree.[/dim]")
            return
            
        styled_lines = []
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                styled_lines.append(f"[green]{escape(line)}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                styled_lines.append(f"[red]{escape(line)}[/red]")
            elif line.startswith("@@"):
                styled_lines.append(f"[cyan]{escape(line)}[/cyan]")
            elif line.startswith("diff --git"):
                styled_lines.append(f"[bold white]{escape(line)}[/bold white]")
            else:
                styled_lines.append(escape(line))
                
        if len(styled_lines) > 200:
            styled_lines = styled_lines[:200] + ["[dim]... (truncated)[/dim]"]
            
        ctx.console.print(Panel("\n".join(styled_lines), title="[bold cyan]Git Workspace Diff[/bold cyan]", border_style="cyan"))
    except Exception as e:
        ctx.console.print(f"[bold red]Failed to fetch git diff:[/bold red] {e}")


from pathlib import Path


@registry.register("history", "Show session history of runs")
def cmd_history(ctx: CommandContext, args: List[str]) -> None:
    if not ctx.state.history:
        ctx.console.print("[dim]No runs found in current session.[/dim]")
        return

    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Run ID", style="bold green", no_wrap=True)
    table.add_column("Agent", style="bold yellow")
    table.add_column("Prompt Preview", style="white")

    for idx, run in enumerate(ctx.state.history):
        content = run.get("prompt", "")
        preview = (content[:60] + "...") if len(content) > 60 else content
        table.add_row(f"#{idx}", run.get("agent_id", "unknown"), preview.replace("\n", " "))

    ctx.console.print(Panel(table, title="[bold cyan]Execution History[/bold cyan]", border_style="cyan"))


@registry.register("replay", "Replay a previous prompt run. Usage: /replay <id>")
def cmd_replay(ctx: CommandContext, args: List[str]) -> Optional[str]:
    if not args:
        ctx.console.print("[yellow]Usage: /replay <id>. Use `/history` to find IDs.[/yellow]")
        return None

    try:
        idx = int(args[0])
        if idx < 0 or idx >= len(ctx.state.history):
            ctx.console.print(f"[bold red]Error: Invalid run ID #{idx}.[/bold red]")
            return None
        
        run = ctx.state.history[idx]
        prompt = run.get("prompt", "")
        agent = run.get("agent_id", ctx.state.active_agent)
        
        ctx.state.active_agent = agent
        ctx.state.delegation_chain = [agent]
        ctx.state.save()
        
        ctx.console.print(f"[bold yellow]Replaying prompt on agent [cyan]{agent}[/cyan]:[/bold yellow]")
        ctx.console.print(f"[dim]{prompt}[/dim]\n")
        return prompt
    except ValueError:
        ctx.console.print("[bold red]Error: ID must be an integer.[/bold red]")
        return None


@registry.register("dashboard", "Show the system dashboard widget")
def cmd_dashboard(ctx: CommandContext, args: List[str]) -> None:
    # Resolve values needed for dashboard
    system_stats = ctx.get_system_stats()
    
    resp = ctx.call_api("/readyz", "GET")
    backend_ok = resp is not None
    ollama_ok = False
    if backend_ok:
        try:
            ollama_ok = resp.json().get("checks", {}).get("ollama_reachable", False)
        except Exception:
            pass
            
    dashboard_panel = render_dashboard(
        state=ctx.state,
        system_stats=system_stats,
        backend_ok=backend_ok,
        ollama_ok=ollama_ok,
        installed_models=ctx.installed_models
    )
    ctx.console.print(dashboard_panel)


@registry.register("mode", "Set console mode. Usage: /mode safe|dev")
def cmd_mode(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(f"Current mode: [bold cyan]{ctx.state.mode.upper()}[/bold cyan]")
        return

    arg = args[0].lower()
    if arg in ("safe", "dev"):
        ctx.state.mode = arg
        ctx.state.save()
        ctx.console.print(f"[green]✓ Mode switched to[/green] [bold cyan]{arg.upper()}[/bold cyan]")
    else:
        ctx.console.print("[yellow]Usage: /mode safe|dev[/yellow]")


@registry.register("tools", "List available agent tools, or dynamically create one. Usage: /tools [create <name>]")
def cmd_tools(ctx: CommandContext, args: List[str]) -> None:
    import re
    from pathlib import Path
    from rich.markup import escape
    
    if args and args[0].lower() == "create":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /tools create <name>[/yellow]")
            return
            
        tool_name = args[1].lower().strip()
        if not re.match(r'^[a-z0-9_]+$', tool_name):
            ctx.console.print("[bold red]Error: Tool name must be alphanumeric with underscores only.[/bold red]")
            return
            
        from rich.prompt import Prompt
        description = Prompt.ask("Describe what this capability should do")
        if not description.strip():
            ctx.console.print("[yellow]Cancelled: Description cannot be empty.[/yellow]")
            return
            
        ctx.console.print(f"[bold cyan]Synthesizing capability [green]{tool_name}[/green]...[/bold cyan]")
        
        class_name = "".join(part.capitalize() for part in tool_name.split("_")) + "Handler"
        
        prompt = f"""
        Generate a Python class named {class_name} conforming to the Swarm OS capability pattern.
        The class must have an async `execute(self, payload: Any) -> Dict[str, Any]` method.
        
        Requirements for the tool:
        {description}
        
        Return ONLY valid python code inside a single ```python ``` codeblock. Do not include any explanations before or after the codeblock.
        """
        
        model = ctx.state.active_model or "qwen2.5:7b-instruct"
        code = ""
        try:
            resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
            if resp and resp.status_code == 200:
                resp_text = resp.json().get("response", "")
                m = re.search(r'```python\s*(.*?)\s*```', resp_text, re.DOTALL)
                if m:
                    code = m.group(1).strip()
                else:
                    code = resp_text.strip()
            else:
                ctx.console.print("[bold red]Error: Failed to contact generator model.[/bold red]")
                return
        except Exception as e:
            ctx.console.print(f"[bold red]Error calling generator: {e}[/bold red]")
            return
            
        if not code:
            ctx.console.print("[bold red]Error: Generated code is empty.[/bold red]")
            return
            
        from rich.prompt import Confirm
        ctx.console.print(Panel(escape(code), title=f"Generated Code for {tool_name}.py", border_style="cyan"))
        
        if ctx.state.mode == "safe":
            if not Confirm.ask("Do you want to write and register this capability?"):
                ctx.console.print("[yellow]Tool creation cancelled by user.[/yellow]")
                return
                
        capabilities_dir = Path(__file__).parent.parent / "swarm_os" / "capabilities"
        file_path = capabilities_dir / f"{tool_name}.py"
        try:
            capabilities_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(code, encoding="utf-8")
            ctx.console.print(f"[green]✓ Successfully wrote capability to [bold]{file_path}[/bold][/green]")
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to write capability file: {e}[/bold red]")
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
        
    ctx.console.print(Panel(table, title=f"[bold cyan]Tool Capabilities ({d.get('count', 0)})[/bold cyan]", border_style="cyan"))


@registry.register("plan", "Show plan details from the last run, or draft structured templates. Usage: /plan [create <objective>]")
def cmd_plan(ctx: CommandContext, args: List[str]) -> None:
    if args and args[0].lower() == "create":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /plan create <objective>[/yellow]")
            return
            
        objective = " ".join(args[1:])
        ctx.console.print(f"[bold cyan]Generating structured developer templates for: [green]{objective}[/green]...[/bold cyan]")
        
        prompt = f"""
        You are an elite software architect. Create a structured markdown Implementation Plan for the objective: "{objective}".
        
        Structure your plan as follows:
        # Goal Description
        - Summary of changes
        ## Proposed Changes
        - Specify the exact files to modify and what changes to make in each.
        ## Verification Plan
        - Tests to run and manual verification steps.
        
        Return ONLY valid markdown text.
        """
        
        model = ctx.state.active_model or "qwen2.5:7b-instruct"
        try:
            resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
            if resp and resp.status_code == 200:
                plan_text = resp.json().get("response", "").strip()
                
                # Write files
                docs_dir = Path(__file__).parent.parent / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                
                plan_file = docs_dir / "implementation_plan.md"
                task_file = docs_dir / "task.md"
                walkthrough_file = docs_dir / "walkthrough.md"
                
                plan_file.write_text(plan_text, encoding="utf-8")
                
                # Create a checklist template
                task_lines = [
                    f"# Task Checklist: {objective}",
                    "- [ ] Phase 1: Preparation",
                    "- [ ] Phase 2: Implementation",
                    "- [ ] Phase 3: Testing & Verification",
                ]
                task_file.write_text("\n".join(task_lines), encoding="utf-8")
                
                # Create a walkthrough template
                walkthrough_lines = [
                    f"# Walkthrough: {objective}",
                    "## Summary of Changes Made",
                    "## Verification & Test Results",
                ]
                walkthrough_file.write_text("\n".join(walkthrough_lines), encoding="utf-8")
                
                ctx.console.print(Panel(plan_text, title="Generated Implementation Plan", border_style="green"))
                ctx.console.print(f"[bold green]✓ Templates successfully created under docs/ directory:[/bold green]")
                ctx.console.print(f" - [cyan]docs/implementation_plan.md[/cyan]")
                ctx.console.print(f" - [cyan]docs/task.md[/cyan]")
                ctx.console.print(f" - [cyan]docs/walkthrough.md[/cyan]")
            else:
                ctx.console.print("[bold red]Error: Failed to contact generator model.[/bold red]")
        except Exception as e:
            ctx.console.print(f"[bold red]Error generating plan templates: {e}[/bold red]")
        return

    # Try to find the last assistant plan in history
    plan_found = False
    for run in reversed(ctx.state.history):
        response = run.get("response", "")
        if "<plan>" in response and "</plan>" in response:
            import re
            m = re.search(r"<plan>(.*?)</plan>", response, re.DOTALL)
            if m:
                ctx.console.print(Panel(m.group(1).strip(), title="Last Model Plan", border_style="yellow"))
                plan_found = True
                break
                
    if not plan_found:
        ctx.console.print("[dim]No plan details found in history.[/dim]")


@registry.register("patch", "Surgically edit a file by replacing a block of code. Usage: /patch <file_path>")
def cmd_patch(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /patch <file_path>. Example: /patch app/main.py[/yellow]")
        return
        
    file_arg = args[0]
    project_root = Path(__file__).parent.parent.resolve()
    file_path = Path(file_arg)
    if not file_path.is_absolute():
        file_path = project_root / file_path
        
    if not file_path.exists() or not file_path.is_file():
        ctx.console.print(f"[bold red]Error: File not found: {file_arg}[/bold red]")
        return

    from rich.prompt import Prompt
    ctx.console.print(f"[bold cyan]Patching file: [green]{file_path.name}[/green]...[/bold cyan]")
    
    target_content = Prompt.ask("Enter the EXACT code block to replace (use '\\n' for newlines)")
    target_content = target_content.replace("\\n", "\n")
    
    if not target_content:
        ctx.console.print("[yellow]Cancelled: Target content cannot be empty.[/yellow]")
        return
        
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        ctx.console.print(f"[bold red]Error reading file: {e}[/bold red]")
        return
        
    occurrences = content.count(target_content)
    if occurrences == 0:
        ctx.console.print("[bold red]Error: Target content not found in the file. Make sure indentation matches exactly.[/bold red]")
        return
    elif occurrences > 1:
        ctx.console.print(f"[bold red]Error: Target content is ambiguous ({occurrences} occurrences found). Please specify a unique block.[/bold red]")
        return

    replacement_content = Prompt.ask("Enter the NEW replacement code block (use '\\n' for newlines)")
    replacement_content = replacement_content.replace("\\n", "\n")
    
    from rich.prompt import Confirm
    if ctx.state.mode == "safe":
        if not Confirm.ask("[bold yellow]Are you sure you want to apply this patch?[/bold yellow]"):
            ctx.console.print("[yellow]Cancelled: Patch not applied.[/yellow]")
            return

    try:
        new_content = content.replace(target_content, replacement_content)
        file_path.write_text(new_content, encoding="utf-8")
        ctx.console.print(f"[bold green]✓ Patch successfully applied to {file_path.name}![/bold green]")
    except Exception as e:
        ctx.console.print(f"[bold red]Error writing file: {e}[/bold red]")


@registry.register("heal", "Evaluate and trigger self-heal run. Usage: /heal run")
def cmd_heal(ctx: CommandContext, args: List[str]) -> None:
    if not args or args[0].lower() != "run":
        ctx.console.print("To run healing cycle: `/heal run`")
        return

    ctx.console.print("[bold magenta]Initiating autonomous self-heal...[/bold magenta]")
    resp = ctx.call_api("/healing/evaluate", "POST")
    if not resp:
        ctx.console.print("[red]✗ Heal endpoint unreachable[/red]")
        return
        
    d = resp.json()
    color = "green" if d.get("last_heal_success", True) else "red"
    
    table = Table(show_header=False, box=SIMPLE)
    table.add_row("Readiness", f"{d.get('recovery_readiness', 0)}%")
    table.add_row("Active Anomalies", str(d.get("active_anomalies", 0)))
    table.add_row("Last Heal", f"[{color}]Success[/{color}]" if d.get("last_heal_success", True) else "[red]Failed[/red]")
    
    ctx.console.print(Panel(table, title="[bold magenta]Healing Cycle Evaluation[/bold magenta]", border_style="magenta"))


@registry.register("goal", "Run an autonomous self-correcting loop to achieve a goal. Usage: /goal <objective>")
def cmd_goal(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /goal <objective>. Example: /goal fix failing tests[/yellow]")
        return
        
    objective = " ".join(args)
    if ctx.run_goal_loop:
        ctx.run_goal_loop(objective)
    else:
        ctx.console.print("[red]Error: Autonomous goal loop is not configured in this context.[/red]")


@registry.register("prev", "Step back in execution history to inspect past decisions")
def cmd_prev(ctx: CommandContext, args: List[str]) -> None:
    if not ctx.state.history:
        ctx.console.print("[yellow]No history available.[/yellow]")
        return
        
    if ctx.state.history_pointer == -1:
        ctx.state.history_pointer = len(ctx.state.history) - 1
        
    if ctx.state.history_pointer <= 0:
        ctx.console.print("[yellow]Already at the earliest run in history.[/yellow]")
        return
        
    ctx.state.history_pointer -= 1
    ctx.state.save()
    
    run = ctx.state.history[ctx.state.history_pointer]
    ctx.console.print(Panel(
        f"[bold yellow]Agent[/bold yellow]: {run.get('agent_id', 'unknown')}\n"
        f"[bold yellow]Prompt[/bold yellow]: {escape(run.get('prompt', ''))}\n"
        f"[bold yellow]Response[/bold yellow]: {escape(run.get('response', '')[:500])}...",
        title=f"[bold cyan]History Explorer (Run #{ctx.state.history_pointer})[/bold cyan]",
        border_style="cyan"
    ))


@registry.register("next", "Step forward in execution history to inspect past decisions")
def cmd_next(ctx: CommandContext, args: List[str]) -> None:
    if not ctx.state.history:
        ctx.console.print("[yellow]No history available.[/yellow]")
        return
        
    if ctx.state.history_pointer == -1 or ctx.state.history_pointer >= len(ctx.state.history) - 1:
        ctx.console.print("[yellow]Already at the latest run in history.[/yellow]")
        return
        
    ctx.state.history_pointer += 1
    ctx.state.save()
    
    run = ctx.state.history[ctx.state.history_pointer]
    ctx.console.print(Panel(
        f"[bold yellow]Agent[/bold yellow]: {run.get('agent_id', 'unknown')}\n"
        f"[bold yellow]Prompt[/bold yellow]: {escape(run.get('prompt', ''))}\n"
        f"[bold yellow]Response[/bold yellow]: {escape(run.get('response', '')[:500])}...",
        title=f"[bold cyan]History Explorer (Run #{ctx.state.history_pointer})[/bold cyan]",
        border_style="cyan"
    ))


@registry.register("debate", "Run an agent planning debate before executing changes. Usage: /debate <goal>")
def cmd_debate(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /debate <goal>. Example: /debate design self-healing cache[/yellow]")
        return
        
    goal = " ".join(args)
    if ctx.run_debate:
        ctx.run_debate(goal)
    else:
        ctx.console.print("[red]Error: Agent debates are not configured in this context.[/red]")


class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_Import(self, node: ast.Import):
        for name in node.names:
            self.imports.append((name.name, 0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.append((node.module, node.level or 0))
            for alias in node.names:
                self.imports.append((f"{node.module}.{alias.name}", node.level or 0))
        self.generic_visit(node)


def resolve_module_path(module_name: str, level: int, current_file: Path, project_root: Path) -> Optional[Path]:
    try:
        if level > 0:
            base_dir = current_file.parent
            for _ in range(level - 1):
                if base_dir.parent == base_dir:
                    break
                base_dir = base_dir.parent
            if module_name:
                rel_path = base_dir / module_name.replace('.', '/')
            else:
                rel_path = base_dir
        else:
            if not module_name:
                return None
            rel_path = project_root / module_name.replace('.', '/')

        py_file = rel_path.with_suffix('.py')
        if py_file.exists() and py_file.is_file():
            return py_file.resolve()

        init_file = rel_path / '__init__.py'
        if init_file.exists() and init_file.is_file():
            return init_file.resolve()

        if rel_path.exists() and rel_path.is_dir():
            return rel_path.resolve()
    except Exception:
        pass
    return None


def get_forward_dependencies(file_path: Path, project_root: Path, depth: int = 1, max_depth: int = 3, visited: Optional[Set[Path]] = None) -> List[Tuple[Path, int]]:
    import ast
    if visited is None:
        visited = set()
    if file_path in visited or depth > max_depth:
        return []
    visited.add(file_path)
    
    deps = []
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content)
        visitor = ImportVisitor()
        visitor.visit(tree)
        for mod, lvl in visitor.imports:
            resolved = resolve_module_path(mod, lvl, file_path, project_root)
            if resolved and resolved.exists() and resolved != file_path:
                deps.append((resolved, depth))
                deps.extend(get_forward_dependencies(resolved, project_root, depth + 1, max_depth, visited))
    except Exception:
        pass
    return deps


def get_reverse_dependencies(target_file: Path, project_root: Path) -> List[Path]:
    import ast
    rev_deps = []
    target_resolved = target_file.resolve()
    target_stem = target_file.stem
    
    for py_file in project_root.rglob('*.py'):
        if '.venv' in py_file.parts or 'tests' in py_file.parts or '__pycache__' in py_file.parts:
            continue
        if py_file.resolve() == target_resolved:
            continue
        try:
            # Performance Optimization: only read/parse if target file's stem name is present in content
            content = py_file.read_text(encoding='utf-8', errors='ignore')
            if target_stem not in content:
                continue
                
            tree = ast.parse(content)
            visitor = ImportVisitor()
            visitor.visit(tree)
            for mod, lvl in visitor.imports:
                resolved = resolve_module_path(mod, lvl, py_file, project_root)
                if resolved and resolved.resolve() == target_resolved:
                    rev_deps.append(py_file)
                    break
        except Exception:
            pass
    return rev_deps


@registry.register("impact", "Show static AST import dependency impact of a file. Usage: /impact <file_path>")
def cmd_impact(ctx: CommandContext, args: List[str]) -> None:
    import ast
    from rich.tree import Tree
    from rich.markup import escape
    
    if not args:
        ctx.console.print("[yellow]Usage: /impact <file_path>. Example: /impact organism_console/renderer.py[/yellow]")
        return
        
    file_arg = args[0]
    project_root = Path(__file__).parent.parent.resolve()
    file_path = Path(file_arg)
    if not file_path.is_absolute():
        file_path = project_root / file_path
        
    if not file_path.exists() or not file_path.is_file():
        ctx.console.print(f"[bold red]Error: File not found: {file_arg}[/bold red]")
        return
        
    ctx.console.print(f"[bold cyan]Analyzing AST dependencies for [green]{file_path.name}[/green]...[/bold cyan]")
    
    forward = get_forward_dependencies(file_path, project_root)
    reverse = get_reverse_dependencies(file_path, project_root)
    
    tree = Tree(f"[bold green]{escape(file_path.name)}[/bold green] ({file_path.relative_to(project_root)})")
    
    fw_node = tree.add("[bold yellow]Forward Dependencies (Imports)[/bold yellow]")
    if forward:
        seen = set()
        for path, depth in forward:
            try:
                rel = path.relative_to(project_root)
            except ValueError:
                rel = path
            if rel not in seen:
                seen.add(rel)
                fw_node.add(f"[cyan]{escape(path.name)}[/cyan] ({rel}) [dim]depth={depth}[/dim]")
    else:
        fw_node.add("[dim]None (or external standard library imports only)[/dim]")
        
    rv_node = tree.add("[bold red]Reverse Dependencies / Impacted Files (Imported By)[/bold red]")
    if reverse:
        for path in sorted(reverse):
            try:
                rel = path.relative_to(project_root)
            except ValueError:
                rel = path
            rv_node.add(f"[orange3]{escape(path.name)}[/orange3] ({rel})")
    else:
        rv_node.add("[dim]None (no local project files import this file)[/dim]")
        
    ctx.console.print(Panel(tree, title="[bold cyan]AST Dependency Impact Map[/bold cyan]", border_style="cyan"))


@registry.register("vote", "Query multiple models on a prompt and show consensus. Usage: /vote <prompt>")
def cmd_vote(ctx: CommandContext, args: List[str]) -> None:
    import re
    import concurrent.futures
    from rich.markup import escape
    
    if not args:
        ctx.console.print("[yellow]Usage: /vote <prompt>. Example: /vote is python statically typed?[/yellow]")
        return
        
    prompt = " ".join(args)
    
    models = list(ctx.installed_models)
    if not models:
        resp = ctx.call_api("/status")
        if resp and resp.status_code == 200:
            models = resp.json().get("installed_models", [])
            
    if not models:
        models = ["qwen2.5:7b-instruct", "qwen2.5-coder:7b", "qwen2.5:3b-instruct"]
        
    targets = models[:3]
    while len(targets) < 3:
        targets.append(targets[0])
        
    ctx.console.print(f"[bold cyan]Submitting consensus vote queries to targets: {', '.join(targets)}...[/bold cyan]")
    
    def run_query(model_name: str) -> Tuple[str, str]:
        try:
            resp = ctx.call_api("/generate", "POST", {"model": model_name, "prompt": prompt})
            if resp and resp.status_code == 200:
                return resp.json().get("response", "").strip(), model_name
            return f"Error: Backend returned status code {resp.status_code if resp else 'None'}", model_name
        except Exception as e:
            return f"Error: {e}", model_name
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_query, m) for m in targets]
        results = [f.result() for f in futures]
        
    def compute_jaccard(text1: str, text2: str) -> float:
        w1 = set(re.findall(r'\w+', text1.lower()))
        w2 = set(re.findall(r'\w+', text2.lower()))
        if not w1 or not w2:
            return 0.0
        return len(w1.intersection(w2)) / len(w1.union(w2))
        
    r1, m1 = results[0]
    r2, m2 = results[1]
    r3, m3 = results[2]
    
    s12 = compute_jaccard(r1, r2)
    s23 = compute_jaccard(r2, r3)
    s13 = compute_jaccard(r1, r3)
    
    a1 = (s12 + s13) / 2.0
    a2 = (s12 + s23) / 2.0
    a3 = (s13 + s23) / 2.0
    
    overall_consensus = (s12 + s23 + s13) / 3.0
    
    agreement_scores = [(a1, r1, m1), (a2, r2, m2), (a3, r3, m3)]
    best_score, best_response, best_model = max(agreement_scores, key=lambda x: x[0])
    
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Model", style="bold green")
    table.add_column("Agreement Score", style="bold yellow")
    table.add_column("Response Preview", style="white")
    
    table.add_row(m1, f"{a1:.2%}", escape(r1[:80] + "..." if len(r1) > 80 else r1))
    table.add_row(m2, f"{a2:.2%}", escape(r2[:80] + "..." if len(r2) > 80 else r2))
    table.add_row(m3, f"{a3:.2%}", escape(r3[:80] + "..." if len(r3) > 80 else r3))
    
    ctx.console.print(table)
    ctx.console.print(f"[bold cyan]Overall Consensus Agreement Rate:[/bold cyan] [bold yellow]{overall_consensus:.2%}[/bold yellow]\n")
    
    ctx.console.print(Panel(
        escape(best_response),
        title=f"[bold green]Consensus Winner: {best_model} (Agreement: {best_score:.2%})[/bold green]",
        border_style="green"
    ))


@registry.register("exit", "Exit the terminal session", aliases=["quit", "bye"])
def cmd_exit(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.save()
    ctx.console.print("[bold blue]Zenith Swarm Control Terminal terminated.[/bold blue]")
    import sys
    sys.exit(0)
