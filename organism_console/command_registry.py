# organism_console/command_registry.py
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import ast
import re
import concurrent.futures
import subprocess
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.box import SIMPLE
from rich.markup import escape

from organism_console.renderer import render_dashboard

def run_syntax_checks(root: Path) -> tuple[bool, str]:
    try:
        git_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5
        )
        if git_diff.returncode == 0:
            modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
            for f in modified_files:
                file_path = root / f
                if file_path.suffix == ".py" and file_path.exists():
                    content = ""
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        ast.parse(content, filename=str(file_path))
                    except SyntaxError as exc:
                        lines = content.splitlines()
                        err_line = exc.lineno
                        context_lines = []
                        if err_line:
                            start = max(0, err_line - 4)
                            end = min(len(lines), err_line + 3)
                            for idx in range(start, end):
                                prefix = ">>> " if idx + 1 == err_line else "    "
                                context_lines.append(f"{prefix}{idx+1}: {lines[idx]}")
                        context_str = "\n".join(context_lines)
                        return False, f"File: {f}\nError: {exc.msg} at line {exc.lineno}\nCode Context:\n```python\n{context_str}\n```"
    except Exception as e:
        return False, f"Syntax checks crashed: {e}"
    return True, ""



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
        self.get_system_stats = get_system_stats
        self.installed_models = installed_models
        self.run_goal_loop = run_goal_loop
        self.run_debate = run_debate
        self.run_prompt_with_agent: Optional[Callable[[str, str, Any], Any]] = None


def route_natural_language_keywords(raw: str) -> tuple[Optional[str], list[str]]:
    clean = raw.lower().strip().strip("?!.")
    
    # Simple direct mappings (fast path)
    if clean in ("show diff", "diff", "git diff", "what changed", "changes", "show changes", "show me what changed"):
        return "diff", []
        
    if clean in ("status", "health", "check health", "system status", "check status", "how is the system"):
        return "status", []
        
    if clean in ("help", "commands", "what can you do", "show commands", "list commands", "menu"):
        return "help", []
        
    if clean in ("commit", "make a commit", "git commit", "save changes", "commit changes"):
        return "commit", []
        
    if clean in ("heal", "self heal", "run healing", "fix system", "run heal"):
        return "heal", ["run"]
        
    if clean in ("clear", "reset", "clear history", "reset context", "start over"):
        return "clear", []
        
    if clean in ("learn", "learn from history", "run learning", "offline learn"):
        return "learn", []
        
    if clean in ("autofix", "auto fix", "fix yourself", "fix bugs", "heal bugs", "fix all", "repair all", "repair everything"):
        return "autofix", []

    if clean in ("cures", "repair knowledge", "known fixes", "what can you fix"):
        return "cures", []

    if clean in ("repair stats", "repair statistics", "heal stats", "self heal stats"):
        return "repair-stats", []

    if clean in ("model", "models", "picker"):
        return "picker", []

    if clean in ("perf", "performance"):
        return "perf", []

    if clean in ("exit", "quit", "bye", "close", "shutdown"):
        return "exit", []
        
    if clean in ("tokens", "token usage", "cost", "how many tokens"):
        return "tokens", []

    if clean in ("agents", "list agents", "show agents", "what agents", "agent list"):
        return "agents", []

    if clean in ("benchmark", "run benchmark", "test models"):
        return "benchmark", []

    if clean in ("run simulation", "simulate", "start simulation"):
        return "simulation", ["run"]
        
    if clean in ("simulation status", "check simulation"):
        return "simulation", ["status"]

    m_search = re.match(r"^(?:search for|find|search memory for)\s+(.+)$", clean)
    if m_search:
        return "search", [m_search.group(1)]

    m_upwork = re.match(r"^(?:analyze upwork job|analyze upwork|upwork)\s+(.+)$", clean)
    if m_upwork:
        return "upwork", [m_upwork.group(1)]

    m_chat_search = re.match(r"^(?:ask librarian|chat search)\s+(.+)$", clean)
    if m_chat_search:
        return "chat-search", [m_chat_search.group(1)]

    # Regex matches
    m_debate = re.match(r"^(?:debate about|discuss|debate|talk about)\s+(.+)$", clean)
    if m_debate:
        return "debate", [m_debate.group(1)]
        
    # Check for direct action instruction prefixes to run in the autonomous goal loop
    goal_words = ("fix", "implement", "add", "create", "refactor", "change", "run tests", "verify", "debug", "test")
    if any(clean.startswith(w) for w in goal_words):
        # We need to map to goal, with the original untouched line as the goal argument
        return "goal", [raw.strip()]
        
    return None, []

def classify_intent_with_llm(raw: str, ctx: CommandContext) -> tuple[Optional[str], list[str]]:
    
    model = "qwen-tuned"
    if ctx.installed_models:
        for m in ctx.installed_models:
            if "4b" in m.lower() and "qwen" in m.lower():
                model = m
                break
            if "llama3-groq" in m.lower() or "deepseek-r1-tool" in m.lower():
                model = m
                break
            if "qwen-tuned" in m.lower() or "ministral" in m.lower():
                model = m
                break
        else:
            model = ctx.installed_models[0]
            
    prompt = (
        "You are the intent routing classification system for Swarm OS CLI. "
        "Classify the following natural language user input into one of these commands:\n"
        "- \"/diff\" (if they want to see git changes, modifications, diff)\n"
        "- \"/status\" (if they want to check health, status, system health)\n"
        "- \"/commit\" (if they want to save changes to git or commit)\n"
        "- \"/heal run\" (if they want to run a healing/repair cycle)\n"
        "- \"/clear\" (if they want to clear history or reset the context)\n"
        "- \"/exit\" (if they want to quit or close the terminal)\n"
        "- \"/tokens\" (if they want to check token count or session cost)\n"
        "- \"/benchmark\" (if they want to run model latency benchmarks)\n"
        "- \"/debate <goal>\" (if they want to discuss or debate a development plan/objective)\n"
        "- \"/goal <goal>\" (if they want to execute an instruction, write code, fix something, add a feature, refactor, run tests, or debug)\n"
        "- \"/chat\" (if it's a general question, discussion, explanation request, or just talking to you)\n\n"
        f"Input: \"{raw}\"\n\n"
        "Return a JSON object with keys:\n"
        "- \"command\": the selected slash command (e.g. \"/diff\", \"/goal fix the routes\", \"/chat\")\n"
        "- \"confidence\": float between 0.0 and 1.0\n\n"
        "Return ONLY the valid JSON, no explanations before or after."
    )
    
    try:
        r = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if r and r.status_code == 200:
            data = r.json()
            response_text = data.get("response", "").strip()
            if "```" in response_text:
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
                if m:
                    response_text = m.group(1)
            parsed = json.loads(response_text)
            command_str = parsed.get("command", "/chat").strip()
            confidence = float(parsed.get("confidence", 0.0))
            
            if confidence >= 0.7 and command_str.startswith("/"):
                parts = command_str.split()
                cmd = parts[0][1:].lower()
                args = parts[1:]
                
                if cmd in ("goal", "debate"):
                    args = [raw.strip()]
                    
                return cmd, args
    except Exception:
        pass
        
    return None, []


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
        
        cmd_name = None
        args = []
        if not raw.startswith("/"):
            # 1. Fast-path keyword matching
            cmd_name, args = route_natural_language_keywords(raw)
            # 2. LLM-based intent routing if fast-path is not found
            if not cmd_name:
                cmd_name, args = classify_intent_with_llm(raw, ctx)
                
            if cmd_name:
                if cmd_name == "chat":
                    return raw
                    
                if cmd_name in self.commands:
                    ctx.console.print(f"[bold cyan]ℹ Auto-Routing intent to command: [green]/{cmd_name} {' '.join(args)}[/green][/bold cyan]")
                    # Record entered command to command history
                    ctx.state.command_history.append(f"/{cmd_name} {' '.join(args)}")
                    ctx.state.save()
                    cmd_info = self.commands[cmd_name]
                    try:
                        return cmd_info["func"](ctx, args)
                    except Exception as e:
                        ctx.console.print(f"[bold red]Command failed:[/bold red] {e}")
                        ctx.state.last_error = str(e)
                        ctx.state.save()
                        return None
                else:
                    ctx.console.print(f"[bold red]Unknown command:[/bold red] /{cmd_name}. Type `/help` to list commands.")
                    return None
            else:
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


@registry.register("agent", "Switch the active agent. Usage: /agent <name> (or /agent list)")
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
        ctx.console.print(f"Active agent: [bold cyan]{ctx.state.active_agent}[/bold cyan]")
        ctx.console.print(f"Available: {agent_list}")
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
        
        from datetime import datetime, timezone
        export_dir = Path(__file__).parent.parent / "swarm_os" / "logs"
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
                role = run.get('role', 'unknown')
                content = run.get('content', '')
                lines.extend([
                    f"## Run #{idx} ({role})",
                    "\n### Content",
                    f"```\n{content}\n```",
                    "\n---\n"
                ])
                
            filename.write_text("\n".join(lines), encoding="utf-8")
            ctx.console.print(f"[green]✓ Trace log successfully exported to [bold]{filename}[/bold][/green]")
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
    status = "[bold green]ON[/bold green]" if ctx.state.cloud_enabled else "[bold red]OFF[/bold red]"
    ctx.console.print(f"☁️  Cloud models are now {status}.")

@registry.register("quota", "Set daily cloud token quota (e.g. /quota 100000)")
def cmd_quota(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(f"Current cloud quota: {ctx.state.cloud_token_quota:,} tokens")
        return
    try:
        quota = int(args[0])
        ctx.state.cloud_token_quota = quota
        ctx.state.save()
        ctx.console.print(f"☁️  Cloud token quota set to [bold cyan]{quota:,}[/bold cyan] tokens.")
    except ValueError:
        ctx.console.print("[bold red]Invalid quota amount.[/bold red] Usage: /quota 100000")

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
    
    from organism_console.token_tracker import get_status_segment
    tracker_status = get_status_segment()
    if tracker_status:
        ctx.console.print(Panel(tracker_status, title="[bold magenta]Live Provider Breakdown[/bold magenta]", border_style="magenta"))

@registry.register("tracker", "Show live token tracker and provider status")
def cmd_tracker(ctx: CommandContext, args: List[str]) -> None:
    from organism_console.token_tracker import get_status_segment
    status = get_status_segment()
    if status:
        ctx.console.print(Panel(status, title="[bold cyan]Live Token Tracker[/bold cyan]", border_style="cyan"))
    else:
        ctx.console.print("[dim]Tracker not available.[/dim]")


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
        content = run.get("content", "")
        preview = (content[:60] + "...") if len(content) > 60 else content
        table.add_row(f"#{idx}", run.get("role", "unknown"), preview.replace("\n", " "))

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
        prompt = run.get("content", "")
        agent = run.get("role", ctx.state.active_agent)
        
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
        available_tools=tools_str
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

        # --- Guard 1: AST syntax check (catches malformed code before it reaches disk) ---
        try:
            import ast as _ast
            _ast.parse(code)
        except SyntaxError as _se:
            ctx.console.print(
                f"[bold red]Generated code has a syntax error and was rejected: {_se}\n"
                "Re-run /tools create or edit the description to produce valid Python.[/bold red]"
            )
            return

        # --- Guard 2: Static ban-list (blocks accidental dangerous stdlib patterns) ---
        _BANNED_PATTERNS = [
            "__import__", "importlib.import_module", "subprocess", "os.system",
            "os.popen", "eval(", "exec(", "compile(", "socket.", "ctypes",
        ]
        _code_check = code.lower()
        _hits = [b for b in _BANNED_PATTERNS if b in _code_check]
        if _hits:
            ctx.console.print(
                f"[bold red]Generated code uses banned patterns ({', '.join(_hits)}) and was rejected.\n"
                "If you intentionally need these, write the capability file manually.[/bold red]"
            )
            return

        # --- Guard 3: Shape contract — must define async execute() per capability API ---
        try:
            _tree = _ast.parse(code)
            _has_execute = any(
                isinstance(node, _ast.AsyncFunctionDef) and node.name == "execute"
                for node in _ast.walk(_tree)
            )
            if not _has_execute:
                ctx.console.print(
                    "[bold red]Generated code does not define 'async def execute()' "
                    "and was rejected. The Swarm OS capability contract requires this method.[/bold red]"
                )
                return
        except Exception:
            pass  # Already passed Guard 1; a walk failure is not a security risk

        from rich.prompt import Confirm
        ctx.console.print(Panel(escape(code), title=f"Generated Code for {tool_name}.py", border_style="cyan"))
        
        # Confirmation is ALWAYS required before writing generated code to disk (auto-loaded on next scan),
        # regardless of safe/dev mode -- dev mode skips friction on routine actions, not verification
        # of never-before-run generated code with full execution privileges.
        if not Confirm.ask("Do you want to save this capability to the sandbox for review?"):
            ctx.console.print("[yellow]Tool creation cancelled by user.[/yellow]")
            return
        capabilities_dir = Path(__file__).parent.parent / "swarm_os" / "sandbox_tools"
        file_path = capabilities_dir / f"{tool_name}.py"
        try:
            capabilities_dir.mkdir(parents=True, exist_ok=True)
            ctx.console.print("[yellow]Generated code saved to sandbox_tools/. Please manually verify it before moving it into capabilities/.[/yellow]")
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


@registry.register("mcp", "Manage registered MCP tool servers. Usage: /mcp list | /mcp prune <server_name>")
def cmd_mcp(ctx: CommandContext, args: List[str]) -> None:
    import json
    from pathlib import Path
    from rich.table import Table as RichTable

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
        t = RichTable(header_style="bold cyan")
        t.add_column("Server", style="bold white")
        t.add_column("Command")
        t.add_column("Args")
        if not servers:
            ctx.console.print("[yellow]No MCP servers registered.[/yellow]")
            return
        for name, cfg in servers.items():
            t.add_row(name, cfg.get("command", ""), " ".join(cfg.get("args", [])))
        ctx.console.print(Panel(t, title="[bold cyan]Registered MCP Servers[/bold cyan]", border_style="cyan"))
        return

    if args[0].lower() == "prune" and len(args) >= 2:
        name = args[1]
        if name not in servers:
            ctx.console.print(f"[red]Server '{name}' not found. Use /mcp list to see registered servers.[/red]")
            return
        from rich.prompt import Confirm
        if Confirm.ask(f"[bold yellow]Remove MCP server '{name}' from swarm_config.json?[/bold yellow]"):
            del config["mcp_servers"][name]
            try:
                config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
                ctx.console.print(f"[green]✓ Removed '{name}'. Restart the backend to take effect.[/green]")
            except Exception as e:
                ctx.console.print(f"[red]Failed to write config: {e}[/red]")
        return

    ctx.console.print("[yellow]Usage: /mcp list | /mcp prune <server_name>[/yellow]")


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

    plan_found = False
    for run in reversed(ctx.state.history):
        content = run.get("content", "")
        if run.get("role") == "assistant" and "<plan>" in content and "</plan>" in content:
            m = re.search(r"<plan>(.*?)</plan>", content, re.DOTALL)
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


@registry.register("commit", "Create a Conventional Commit with an AI-generated message. Usage: /commit")
def cmd_commit(ctx: CommandContext, args: List[str]) -> None:
    import subprocess
    project_root = Path(__file__).parent.parent.resolve()
    try:
        # 1. Run git diff to see changes
        diff_res = subprocess.run(["git", "diff"], capture_output=True, text=True, cwd=project_root)
        diff_staged = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True, cwd=project_root)
        
        diff_text = diff_res.stdout + "\n" + diff_staged.stdout
        if not diff_text.strip():
            ctx.console.print("[yellow]No changes detected in repository workspace.[/yellow]")
            return
            
        ctx.console.print("[bold cyan]Analyzing diff and generating Conventional Commit message...[/bold cyan]")
        
        prompt = f"""
        Analyze this git diff and write a concise, professional commit message adhering strictly to Conventional Commits:
        
        {diff_text[:3000]}
        
        Your output must follow this format:
        <type>(<scope>): <short description>
        
        Do not output any introductory or concluding text, only the commit message itself.
        """
        
        model = ctx.state.active_model or "qwen2.5:7b-instruct"
        resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if resp and resp.status_code == 200:
            commit_msg = resp.json().get("response", "").strip().splitlines()[0]
            ctx.console.print(Panel(commit_msg, title="Generated Commit Message", border_style="green"))
            
            from rich.prompt import Confirm
            if Confirm.ask("[bold yellow]Do you want to stage all changes and commit?[/bold yellow]"):
                subprocess.run(["git", "add", "."], cwd=project_root)
                commit_res = subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True, cwd=project_root)
                ctx.console.print(f"[green]✓ git add . executed.[/green]")
                if commit_res.returncode == 0:
                    ctx.console.print(f"[bold green]✓ Successfully committed changes![/bold green]")
                else:
                    ctx.console.print(f"[bold red]Failed to commit: {commit_res.stderr}[/bold red]")
        else:
            ctx.console.print("[bold red]Failed to generate commit message from backend.[/bold red]")
    except Exception as e:
        ctx.console.print(f"[bold red]Commit command error: {e}[/bold red]")


@registry.register("branch", "Create or checkout a Git branch. Usage: /branch <branch_name>")
def cmd_branch(ctx: CommandContext, args: List[str]) -> None:
    import subprocess
    if not args:
        # Show active branch
        project_root = Path(__file__).parent.parent.resolve()
        res = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=project_root)
        if res.returncode == 0:
            ctx.console.print(f"Active branch: [bold green]{res.stdout.strip()}[/bold green]")
        else:
            ctx.console.print("[red]Failed to get current branch.[/red]")
        return
        
    branch_name = args[0].strip()
    project_root = Path(__file__).parent.parent.resolve()
    
    ctx.console.print(f"[bold cyan]Checking out branch [green]{branch_name}[/green]...[/bold cyan]")
    # Try checking out the branch. If it doesn't exist, create it.
    checkout_res = subprocess.run(["git", "checkout", branch_name], capture_output=True, text=True, cwd=project_root)
    if checkout_res.returncode != 0:
        # Try checking out with -b
        checkout_res = subprocess.run(["git", "checkout", "-b", branch_name], capture_output=True, text=True, cwd=project_root)
        
    if checkout_res.returncode == 0:
        ctx.console.print(f"[bold green]✓ Switched to branch '{branch_name}'[/bold green]")
    else:
        ctx.console.print(f"[bold red]Failed to switch branch: {checkout_res.stderr}[/bold red]")


@registry.register("debug", "Run a script/command and analyze failures. Usage: /debug <command>")
def cmd_debug(ctx: CommandContext, args: List[str]) -> None:
    import subprocess
    import sys
    if not args:
        ctx.console.print("[yellow]Usage: /debug <command>. Example: /debug python main.py[/yellow]")
        return
        
    command = args
    ctx.console.print(f"[bold cyan]Executing command: [white]{' '.join(command)}[/white]...[/bold cyan]")
    
    project_root = Path(__file__).parent.parent.resolve()
    # Execute the command
    try:
        res = subprocess.run(command, capture_output=True, text=True, cwd=project_root, timeout=60)
        stdout = res.stdout
        stderr = res.stderr
        exit_code = res.returncode
    except subprocess.TimeoutExpired:
        ctx.console.print("[bold red]Error: Execution timed out (60s limit).[/bold red]")
        return
    except Exception as e:
        ctx.console.print(f"[bold red]Execution error: {e}[/bold red]")
        return
        
    if exit_code == 0:
        ctx.console.print("[bold green]✓ Execution succeeded with exit code 0.[/bold green]")
        if stdout.strip():
            ctx.console.print(Panel(stdout, title="Output", border_style="green"))
        return
        
    ctx.console.print(f"[bold red]✗ Execution failed with exit code {exit_code}.[/bold red]")
    ctx.console.print(Panel(stderr or stdout, title="Error Output / Stacktrace", border_style="red"))
    
    syntax_passed, syntax_error_msg = run_syntax_checks(project_root)
    if not syntax_passed:
        ctx.console.print(Panel(
            syntax_error_msg,
            title="[bold red]⚠️  SYNTAX ERROR DETECTED IN MODIFIED FILES[/bold red]",
            border_style="bold red"
        ))
        
    ctx.console.print("[bold cyan]Submitting failure trace to LLM for automated diagnostic guide...[/bold cyan]")
    
    prompt = f"""
    The following developer command failed:
    Command: {' '.join(command)}
    Exit Code: {exit_code}
    
    Stderr / Traceback:
    {stderr or stdout}
    
    Explain what caused this crash and provide a clear, step-by-step diagnostic guide on how to fix it.
    """
    
    model = ctx.state.active_model or "qwen2.5:7b-instruct"
    resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
    if resp and resp.status_code == 200:
        diag = resp.json().get("response", "").strip()
        ctx.console.print(Panel(diag, title="AI Diagnostic Guide", border_style="yellow"))
        
        from rich.prompt import Confirm
        if Confirm.ask("[bold yellow]Would you like the agent to automatically repair this crash?[/bold yellow]"):
            goal_text = f"Fix the crash/failure of command '{' '.join(command)}' which failed with traceback:\n{stderr or stdout}"
            if ctx.run_goal_loop:
                ctx.run_goal_loop(goal_text)
            else:
                ctx.console.print("[red]Goal loop runner unavailable.[/red]")
    else:
        ctx.console.print("[bold red]Failed to fetch diagnostic guide from backend.[/bold red]")


@registry.register("prompt", "View or override system mandates for an agent. Usage: /prompt <agent_id> [new_mandate]")
def cmd_prompt(ctx: CommandContext, args: List[str]) -> None:
    import json
    if not args:
        ctx.console.print("[yellow]Usage: /prompt <agent_id> [new_mandate]. Example: /prompt coder Be very brief.[/yellow]")
        return
        
    agent_id = args[0].lower()
    project_root = Path(__file__).parent.parent.resolve()
    mandates_file = project_root / "docs" / "agent_mandates.json"
    
    # Load existing custom mandates if any
    custom_mandates = {}
    if mandates_file.exists():
        try:
            custom_mandates = json.loads(mandates_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    if len(args) < 2:
        # Just show current prompt
        if agent_id in custom_mandates:
            ctx.console.print(Panel(custom_mandates[agent_id], title=f"Custom Prompt Mandate for '{agent_id}'", border_style="green"))
        else:
            ctx.console.print(f"[dim]Agent '{agent_id}' has no custom override. It uses the default role mandate.[/dim]")
        return
        
    new_mandate = " ".join(args[1:])
    custom_mandates[agent_id] = new_mandate
    
    try:
        mandates_file.parent.mkdir(parents=True, exist_ok=True)
        mandates_file.write_text(json.dumps(custom_mandates, indent=2), encoding="utf-8")
        ctx.console.print(f"[bold green]✓ Custom system prompt mandate for '{agent_id}' successfully updated![/bold green]")
        ctx.console.print(f"[dim]Backend will automatically apply this override on the next query.[/dim]")
    except Exception as e:
        ctx.console.print(f"[bold red]Error saving prompt mandate override: {e}[/bold red]")


@registry.register("memory", "Query or inject memory into Qdrant store. Usage: /memory query|inject <value>")
def cmd_memory(ctx: CommandContext, args: List[str]) -> None:
    import json
    from datetime import datetime, timezone
    if len(args) < 2:
        ctx.console.print("[yellow]Usage: /memory query <term> OR /memory inject <text>[/yellow]")
        return
        
    action = args[0].lower()
    text = " ".join(args[1:])
    
    if action == "query":
        ctx.console.print(f"[bold cyan]Searching vector memories for: [green]{text}[/green]...[/bold cyan]")
        try:
            import requests
            emb_resp = requests.post("http://127.0.0.1:11434/api/embeddings", json={"model": "nomic-embed-text", "prompt": text[:7000]}, timeout=10.0)
            if emb_resp.status_code == 200:
                vector = emb_resp.json().get("embedding", [0.0]*768)
            else:
                vector = [0.0]*768 # fallback
                
            q_resp = requests.post("http://127.0.0.1:6333/collections/upwork_learning/points/search", json={
                "vector": vector,
                "limit": 5,
                "with_payload": True
            }, timeout=10.0)
            
            if q_resp.status_code == 200:
                results = q_resp.json().get("result", [])
                if not results:
                    ctx.console.print("[dim]No vector memory matches found.[/dim]")
                    return
                table = Table(box=SIMPLE, header_style="bold cyan")
                table.add_column("Score", style="bold yellow")
                table.add_column("Memory Payload", style="white")
                for r in results:
                    score = r.get("score", 0.0)
                    payload = r.get("payload", {})
                    table.add_row(f"{score:.2f}", json.dumps(payload, indent=1))
                ctx.console.print(table)
            else:
                ctx.console.print(f"[bold red]Qdrant search failed with status {q_resp.status_code}.[/bold red]")
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to query memory store: {e}[/bold red]")
            
    elif action == "inject":
        ctx.console.print(f"[bold cyan]Injecting text into vector memory store...[/bold cyan]")
        try:
            import requests
            import uuid
            # Generate embedding
            emb_resp = requests.post("http://127.0.0.1:11434/api/embeddings", json={"model": "nomic-embed-text", "prompt": text[:7000]}, timeout=10.0)
            if emb_resp.status_code == 200:
                vector = emb_resp.json().get("embedding", [0.0]*768)
            else:
                ctx.console.print("[bold red]Failed to generate text embedding from Ollama.[/bold red]")
                return
                
            q_resp = requests.put("http://127.0.0.1:6333/collections/upwork_learning/points", json={
                "points": [{
                    "id": str(uuid.uuid4()),
                    "vector": vector,
                    "payload": {
                        "text": text,
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                }]
            }, timeout=10.0)
            
            if q_resp.status_code == 200:
                ctx.console.print(f"[bold green]✓ Text successfully stored in vector memory![/bold green]")
            else:
                ctx.console.print(f"[bold red]Qdrant upsert failed with status {q_resp.status_code}.[/bold red]")
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to inject memory: {e}[/bold red]")


@registry.register("heal", "Evaluate and trigger self-heal run. Usage: /heal run [force] | /heal stats | /heal lessons [type]")
def cmd_heal(ctx: CommandContext, args: List[str]) -> None:
    from organism_console.core.self_repair_engine import SelfRepairEngine
    from organism_console.core.repair_engine import load_cures, load_lessons

    if not args:
        ctx.console.print("[yellow]Usage: /heal run (force)  |  /heal stats  |  /heal lessons (type)[/yellow]")
        return

    sub = args[0].lower()

    if sub == "stats":
        engine = SelfRepairEngine()
        stats = engine.show_stats()
        table = Table(show_header=False, box=SIMPLE)
        table.add_row("T0 (Pattern)", str(stats["t0_hits"]))
        table.add_row("T1 (Constrained)", str(stats["t1_hits"]))
        table.add_row("T2 (Deep)", str(stats["t2_hits"]))
        table.add_row("Failed", str(stats["failures"]))
        table.add_row("Total Repairs", str(stats["total_repairs"]))
        table.add_row("Tokens Spent", str(stats["tokens_spent"]))
        cures_count = sum(len(v) for v in load_cures().values())
        lessons_count = len(load_lessons())
        table.add_row("Distilled Cures", str(cures_count))
        table.add_row("Historical Lessons", str(lessons_count))
        ctx.console.print(Panel(table, title="[bold magenta]Self-Heal Engine Stats[/bold magenta]", border_style="magenta"))
        return

    if sub == "lessons":
        ftype = args[1] if len(args) > 1 else None
        engine = SelfRepairEngine()
        lessons = engine.show_lessons(ftype)
        if not lessons:
            ctx.console.print("[dim]No lessons recorded yet.[/dim]")
            return
        t = Table(box=SIMPLE, header_style="bold cyan")
        t.add_column("Type", style="bold yellow")
        t.add_column("Tier", justify="right")
        t.add_column("Fixed", style="green")
        t.add_column("Error", style="white")
        t.add_column("Action", style="cyan")
        for l in reversed(lessons[-15:]):
            t.add_row(
                l.get("failure_type", "?"),
                str(l.get("tier_used", "?")),
                "[green]✓[/green]" if l.get("success") else "[red]✗[/red]",
                l.get("error_text", "")[:50],
                (l.get("repair_action") or "")[:50],
            )
        ctx.console.print(Panel(t, title="[bold magenta]Repair History[/bold magenta]", border_style="magenta"))
        return

    if sub != "run":
        ctx.console.print("[yellow]Usage: /heal run (force)  |  /heal stats  |  /heal lessons (type)[/yellow]")
        return

    ctx.console.print("[bold magenta]Initiating autonomous self-heal with tiered repair...[/bold magenta]")
    resp = ctx.call_api("/healing/evaluate", "POST")
    if not resp:
        ctx.console.print("[red]✗ Heal endpoint unreachable, falling back to local diagnostics[/red]")
    else:
        d = resp.json()
        color = "green" if d.get("last_heal_success", True) else "red"
        anomalies = d.get("active_anomalies", 0)
        
        table = Table(show_header=False, box=SIMPLE)
        table.add_row("Readiness", f"{d.get('recovery_readiness', 0)}%")
        table.add_row("Active Anomalies", str(anomalies))
        table.add_row("Last Heal", f"[{color}]Success[/{color}]" if d.get("last_heal_success", True) else "[red]Failed[/red]")
        
        ctx.console.print(Panel(table, title="[bold magenta]Healing Cycle Evaluation[/bold magenta]", border_style="magenta"))
        
        if anomalies == 0 and "force" not in map(str.lower, args):
            ctx.console.print("[green]No anomalies detected. Use `/heal run force` to run anyway.[/green]")
            return

    ctx.console.print("[bold cyan]Running tiered self-repair (T0→T1→T2)...[/bold cyan]")
    engine = SelfRepairEngine(ctx)

    event_file = Path(__file__).parent.parent / "data" / "events" / "events.jsonl"
    if event_file.exists():
        import json as _json
        failures = []
        with open(event_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = _json.loads(line)
                    if data.get("event_type") == "tool_result":
                        res = data.get("payload", {}).get("result", {})
                        if not res.get("ok", False):
                            err = res.get("error", "").strip()
                            if err and len(err) < 500:
                                failures.append(err)
                except Exception:
                    pass

        if failures:
            from collections import Counter as _Counter
            top = _Counter(failures).most_common(5)
            for err, count in top:
                ctx.console.print(f"\n[bold]Error ({count}x):[/bold] {err[:100]}")
                engine.diagnose_and_repair(err)

    stats = engine.show_stats()
    ctx.console.print(f"\n[bold cyan]Heal Summary:[/bold cyan] T0:[green]{stats['t0_hits']}[/green] T1:[green]{stats['t1_hits']}[/green] T2:[green]{stats['t2_hits']}[/green] Failed:[red]{stats['failures']}[/red]")

    cures = load_cures()
    ctx.console.print(f"[dim]Knowledge base: {sum(len(v) for v in cures.values())} distilled cures[/dim]")

    if stats['failures'] > 0 and (anomalies > 0 or "force" in map(str.lower, args)):
        ctx.console.print("\n[bold cyan]Dispatching Internet Self-Healing Goal Loop for unresolved issues...[/bold cyan]")
        if ctx.run_goal_loop:
            goal_payload = (
                "Analyze the system's active anomalies (or recent failures in logs), "
                "use `web_search` to research the root cause and modern syntax fixes on the internet, "
                "and use `filesystem` to autonomously rewrite the broken Python code."
            )
            ctx.run_goal_loop(goal_payload)
        else:
            ctx.console.print("[red]Goal loop runner unavailable.[/red]")


@registry.register("upgrade", "Autonomously go on the internet, find SOTA architectures, and upgrade the Zenith OS codebase")
def cmd_upgrade(ctx: CommandContext, args: List[str]) -> None:
    custom_task = " ".join(args) if args else ""
    objective = (
        "You are tasked with a self-improvement cycle. Use `web_search` to find the absolute cutting-edge state of the art for Python AI agent frameworks (e.g. agent memory, multi-agent routing, or self-healing systems). "
        "Search highly technical sources like GitHub repositories (e.g. 'site:github.com AI agent framework 2026'), Arxiv papers, HuggingFace discussions, and subreddits like r/LocalLLaMA. "
        "Analyze the current Zenith OS codebase. If you find a missing advanced feature or an outdated pattern, write a plan to implement it, and then use `filesystem` to upgrade the codebase."
    )
    if custom_task:
        objective += f"\n\nSPECIFIC USER TASK: {custom_task}"
        
    if ctx.run_goal_loop:
        ctx.console.print(f"[bold magenta]🚀 Initiating Autonomous Self-Upgrade Cycle...[/bold magenta]")
        if custom_task:
            ctx.console.print(f"[cyan]Targeting specific task:[/cyan] {custom_task}")
        ctx.run_goal_loop(objective)
    else:
        ctx.console.print("[red]Error: Autonomous goal loop is not configured in this context.[/red]")

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
    from rich.panel import Panel
    from rich.markup import escape
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
    ctx.console.print(f"[green]Stepped back to history state {ctx.state.history_pointer}.[/green]")
    
    run = ctx.state.history[ctx.state.history_pointer]
    ctx.console.print(Panel(
        f"[bold yellow]Role[/bold yellow]: {run.get('role', 'unknown')}\n"
        f"[bold yellow]Content[/bold yellow]: {escape(run.get('content', '')[:500])}...",
        title=f"[bold cyan]History Explorer (Run #{ctx.state.history_pointer})[/bold cyan]",
        border_style="cyan"
    ))

@registry.register("map", "Map the codebase and save to .swarm_brain/repo_map.md for agents to use")
def cmd_map(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print("[blue]Mapping codebase...[/blue]")
    try:
        import sys
        import os
        from pathlib import Path
        
        # Ensure the project root is in sys.path so we can import runtime_v2
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
            
        from runtime_v2.services.mapper import generate_repo_map
        
        map_content = generate_repo_map(os.getcwd())
        
        brain_dir = Path(".swarm_brain")
        brain_dir.mkdir(exist_ok=True)
        
        map_file = brain_dir / "repo_map.md"
        map_file.write_text(map_content, encoding="utf-8")
        
        # Calculate tokens roughly (1 token ~ 4 chars)
        approx_tokens = len(map_content) // 4
        
        ctx.console.print(f"[green]✔ Codebase mapped successfully! (~{approx_tokens:,} tokens)[/green]")
        ctx.console.print(f"Saved to {map_file}. Agents will now use this for context.")
    except Exception as e:
        ctx.console.print(f"[red]Error mapping codebase: {e}[/red]")


@registry.register("index", "Index the codebase into the local Qdrant vector database for semantic search.")
def cmd_index(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print("[blue]Building semantic codebase index... This may take a moment depending on codebase size.[/blue]")
    try:
        import sys
        import os
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
            
        from runtime_v2.services.indexer import index_codebase
        
        files, chunks = index_codebase(os.getcwd())
        ctx.console.print(f"[green]✔ Codebase successfully indexed! ({files} files, {chunks} semantic chunks)[/green]")
        ctx.console.print("Agents can now use the `semantic_search` tool to query the codebase.")
    except Exception as e:
        ctx.console.print(f"[red]Error indexing codebase: {e}[/red]")
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
        f"[bold yellow]Role[/bold yellow]: {run.get('role', 'unknown')}\n"
        f"[bold yellow]Content[/bold yellow]: {escape(run.get('content', '')[:500])}...",
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
        models = ["qwen-tuned", "qwen-tuned", "qwen-tuned"]
        
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


@registry.register("agents", "List all registered agents and their assigned models")
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
        table.add_column("Model", style="yellow")
        table.add_column("Description", style="white")
        from runtime_v2.services.model_registry import AGENT_MODELS
        for a in agents:
            agent_id = a.get("id", "?")
            assigned_model, _ = AGENT_MODELS.get(agent_id, ("—", ""))
            active = " ▶" if agent_id == ctx.state.active_agent else ""
            table.add_row(
                f"{agent_id}{active}",
                a.get("role", "?"),
                assigned_model,
                a.get("description", "")[:60]
            )
        ctx.console.print(Panel(table, title=f"[bold cyan]Agent Registry ({len(agents)} agents)[/bold cyan]", border_style="cyan"))
    except Exception as e:
        ctx.console.print(f"[bold red]Failed to parse agents:[/bold red] {e}")

@registry.register("exit", "Exit the terminal session", aliases=["quit", "bye"])
def cmd_exit(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.save(sync=True)
    ctx.console.print("[bold blue]Zenith Swarm Control Terminal terminated.[/bold blue]")
    import sys
    sys.exit(0)


@registry.register("benchmark", "Test latencies, token throughput, and success rates across local Ollama models.")
def cmd_benchmark(ctx: CommandContext, args: List[str]) -> None:
    models = ctx.installed_models
    if not models:
        ctx.console.print("[yellow]No installed models found to benchmark.[/yellow]")
        return
        
    if args:
        target_model = args[0]
        matching_models = [m for m in models if target_model.lower() in m.lower()]
        if not matching_models:
            ctx.console.print(f"[yellow]No models matching '{target_model}' found. Available models: {', '.join(models)}[/yellow]")
            return
        models = matching_models
    
    import time
    from concurrent.futures import ThreadPoolExecutor
    
    table = Table(title="[bold cyan]Ollama Models Benchmark Engine[/bold cyan]", border_style="cyan")
    table.add_column("Model", style="bold green")
    table.add_column("Status", style="yellow")
    table.add_column("Latency (s)", style="magenta", justify="right")
    table.add_column("Throughput (t/s)", style="blue", justify="right")
    table.add_column("Output Preview", style="white")
    
    benchmark_prompt = "Explain why gravity is weaker than electromagnetism in exactly 2 sentences."
    
    ctx.console.print(f"[cyan]Initiating concurrent benchmarking across {len(models)} models...[/cyan]")
    
    def test_model(model_name: str):
        start = time.time()
        try:
            payload = {"model": model_name, "prompt": benchmark_prompt}
            resp = ctx.call_api("/generate", "POST", payload=payload)
            duration = time.time() - start
            if resp and resp.status_code == 200:
                data = resp.json()
                response_text = data.get("response", "").strip()
                tokens = max(1, len(response_text) // 4)
                tokens_per_sec = tokens / max(0.1, duration)
                preview = response_text[:50].replace("\n", " ") + "..."
                return model_name, "[green]SUCCESS[/green]", duration, tokens_per_sec, preview
            else:
                return model_name, "[red]FAILED[/red]", duration, 0.0, f"HTTP Status {resp.status_code if resp else 'No response'}"
        except Exception as e:
            duration = time.time() - start
            return model_name, "[red]ERROR[/red]", duration, 0.0, str(e)[:50]

    with ThreadPoolExecutor(max_workers=min(len(models), 4)) as executor:
        results = list(executor.map(test_model, models))
        
    for m, status, dur, tps, prev in results:
        table.add_row(m, status, f"{dur:.2f}s", f"{tps:.1f} t/s", prev)
        
    ctx.console.print(table)


@registry.register("compress", "Summarize older turns in history to free up context window space.")
def cmd_compress(ctx: CommandContext, args: List[str]) -> None:
    history = ctx.state.history
    if not history or len(history) <= 4:
        ctx.console.print("[yellow]History is too short to compress (requires > 4 messages).[/yellow]")
        return
    
    to_summarize = history[:-4]
    keep = history[-4:]
    
    conv_text = ""
    for msg in to_summarize:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        conv_text += f"{role}: {content}\n\n"
        
    prompt = (
        "You are a conversation summarization helper. Provide a very short summary of the following conversation history in exactly 2-3 sentences. "
        "Focus strictly on key actions, decisions, and current focus so the assistant can continue the task without full context.\n\n"
        f"CONVERSATION HISTORY:\n{conv_text}"
    )
    
    fast_model = "qwen-tuned"
    for m in ctx.installed_models:
        if "3b" in m or "7b" in m:
            fast_model = m
            break
            
    ctx.console.print(f"[cyan]Compressing {len(to_summarize)} history messages using [bold green]{fast_model}[/bold green]...[/cyan]")
    
    try:
        payload = {"model": fast_model, "prompt": prompt}
        resp = ctx.call_api("/generate", "POST", payload=payload)
        if resp and resp.status_code == 200:
            summary = resp.json().get("response", "").strip()
            compressed_msg = {
                "role": "system",
                "content": f"[Conversation History Compressed Summary: {summary}]"
            }
            new_history = [compressed_msg] + keep
            ctx.state.history = new_history
            ctx.state.save()
            ctx.console.print("[green]✓ History successfully compressed! Summary prepended to active context.[/green]")
            from rich.panel import Panel
            ctx.console.print(Panel(summary, title="[bold cyan]Compressed Summary[/bold cyan]", border_style="cyan"))
        else:
            ctx.console.print(f"[red]Failed to generate summary: Status {resp.status_code if resp else 'No response'}[/red]")
    except Exception as e:
        ctx.console.print(f"[red]Error during compression: {e}[/red]")


@registry.register("autoassign", "Analyze local models via cloud benchmarks and auto-assign them to agents.")
def cmd_autoassign(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print("[cyan]Requesting dynamic AI auto-assignment from backend...[/cyan]")
    resp = ctx.call_api("/models/autoassign", "POST", {})
    if not resp or resp.status_code != 200:
        ctx.console.print(f"[bold red]Failed to auto-assign:[/bold red] {resp.text if resp else 'No response'}")
        return
    mapping = resp.json().get("mapping", {})
    if not mapping:
        ctx.console.print("[bold red]No valid mapping returned.[/bold red]")
        return
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Agent Role", style="bold green")
    table.add_column("Assigned Model", style="yellow")
    for role, model in mapping.items():
        table.add_row(role, model)
    ctx.console.print(Panel(table, title="[bold cyan]Dynamic Auto-Assignment Complete[/bold cyan]", border_style="cyan"))


@registry.register("local", "Force local-only routing for model fallbacks.")
def cmd_local(ctx: CommandContext, args: List[str]) -> None:
    import os
    os.environ["SWARM_ROUTING_MODE"] = "local_only"
    ctx.console.print("[bold green]Routing mode:[/bold green] local_only")

@registry.register("auto", "Use local-first routing, then cloud fallbacks if needed.")
def cmd_auto(ctx: CommandContext, args: List[str]) -> None:
    import os
    os.environ["SWARM_ROUTING_MODE"] = "auto"
    ctx.console.print("[bold cyan]Routing mode:[/bold cyan] auto")

@registry.register("cloud-on", "Allow cloud escalation in fallback routing.")
def cmd_cloud_on(ctx: CommandContext, args: List[str]) -> None:
    import os
    os.environ["SWARM_ROUTING_MODE"] = "cloud_allowed"
    ctx.console.print("[bold yellow]Routing mode:[/bold yellow] cloud_allowed")

@registry.register("routing", "Show current routing mode.")
def cmd_routing(ctx: CommandContext, args: List[str]) -> None:
    import os
    mode = os.environ.get("SWARM_ROUTING_MODE", "auto")
    ctx.console.print(f"[bold magenta]Routing mode:[/bold magenta] {mode}")


@registry.register("cloud-off", "Disable cloud fallback routing and stay local-only.")
def cmd_cloud_off(ctx: CommandContext, args: List[str]) -> None:
    import os
    os.environ["SWARM_ROUTING_MODE"] = "local_only"
    ctx.console.print("[bold green]Routing mode:[/bold green] local_only")

@registry.register("speak", "Toggle speech feedback")
def cmd_speak(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.speech_enabled = not getattr(ctx.state, "speech_enabled", False)
    ctx.state.save()
    status = "[bold green]ON[/bold green]" if ctx.state.speech_enabled else "[bold red]OFF[/bold red]"
    ctx.console.print(f"Speech feedback is now {status}.")

@registry.register("search", "Semantic search in Qdrant memory")
def cmd_search(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /search <query>[/yellow]")
        return
    query = " ".join(args)
    ctx.console.print(f"[cyan]Searching memory for: '{query}'...[/cyan]")
    resp = ctx.call_api(f"/features/search", "POST", payload={"query": query})
    if resp and resp.status_code == 200:
        data = resp.json().get("results", [])
        if not data:
            ctx.console.print("[dim]No results found.[/dim]")
            return
        from rich.table import Table
        table = Table(title="[bold cyan]Search Results[/bold cyan]")
        table.add_column("Score", style="green")
        table.add_column("Content", style="white")
        for item in data:
            table.add_row(f"{item.get('score', 0):.2f}", str(item.get('content', ''))[:150] + "...")
        ctx.console.print(table)
    else:
        ctx.console.print("[bold red]Search failed.[/bold red]")

@registry.register("upwork", "Analyze an Upwork job description or URL")
def cmd_upwork(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /upwork <url_or_description>[/yellow]")
        return
    job_text = " ".join(args)
    ctx.console.print("[cyan]Running Upwork Scout analysis...[/cyan]")
    resp = ctx.call_api("/features/upwork", "POST", payload={"query": job_text}, stream=True)
    if resp:
        from rich.live import Live
        full_response = ""
        with Live(console=ctx.console, refresh_per_second=10) as live:
            for line in resp.iter_lines():
                if line:
                    chunk = line.decode('utf-8').replace('data: ', '')
                    if chunk == "[DONE]":
                        continue
                    try:
                        import json
                        data = json.loads(chunk)
                        full_response += data.get("content", "")
                        from rich.panel import Panel
                        from rich.markdown import Markdown
                        live.update(Panel(Markdown(full_response), title="[bold cyan]Upwork Scout[/bold cyan]", border_style="cyan"))
                    except Exception:
                        pass
    else:
        ctx.console.print("[bold red]Failed to run Upwork Scout.[/bold red]")

@registry.register("chat-search", "Ask the AI Librarian a question with web/doc search")
def cmd_chat_search(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /chat-search <query>[/yellow]")
        return
    query = " ".join(args)
    ctx.console.print(f"[cyan]Asking AI Librarian: '{query}'...[/cyan]")
    resp = ctx.call_api("/features/chat-search", "POST", payload={"query": query}, stream=True)
    if resp:
        from rich.live import Live
        full_response = ""
        with Live(console=ctx.console, refresh_per_second=10) as live:
            for line in resp.iter_lines():
                if line:
                    chunk = line.decode('utf-8').replace('data: ', '')
                    if chunk == "[DONE]":
                        continue
                    try:
                        import json
                        data = json.loads(chunk)
                        full_response += data.get("content", "")
                        from rich.panel import Panel
                        from rich.markdown import Markdown
                        live.update(Panel(Markdown(full_response), title="[bold cyan]AI Librarian[/bold cyan]", border_style="cyan"))
                    except Exception:
                        pass
    else:
        ctx.console.print("[bold red]Chat search failed.[/bold red]")

@registry.register("simulation", "Manage Swarm Simulation")
def cmd_simulation(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /simulation [run|status][/yellow]")
        return
    action = args[0].lower()
    if action == "run":
        ctx.console.print("[cyan]Triggering simulation run...[/cyan]")
        resp = ctx.call_api("/api/admin/run", "POST", payload={})
        if resp and resp.status_code == 200:
            ctx.console.print("[bold green]Simulation triggered successfully.[/bold green]")
        else:
            ctx.console.print("[bold red]Failed to trigger simulation.[/bold red]")
    elif action == "status":
        ctx.console.print("[cyan]Fetching simulation status...[/cyan]")
        resp = ctx.call_api("/api/admin/generation", "GET")
        if resp and resp.status_code == 200:
            data = resp.json()
            ctx.console.print(f"[green]Generation: {data.get('generation')}[/green]")
            genomes = data.get("top_organisms", [])
            for g in genomes:
                ctx.console.print(f" - {g.get('id')} ({g.get('model')}): Fitness {g.get('fitness')}")
        else:
            ctx.console.print("[bold red]Failed to fetch status.[/bold red]")
    else:
        ctx.console.print("[yellow]Unknown action. Use 'run' or 'status'.[/yellow]")