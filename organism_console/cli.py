# organism_console/cli.py
import sys
import os
import re
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

# Force UTF-8 encoding on standard I/O streams for Unicode stability (e.g. redirected pipes)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import requests
import psutil
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.box import SIMPLE
from rich.rule import Rule
from rich.logging import RichHandler
from rich.status import Status
from rich.markup import escape
from rich.tree import Tree
from rich.markdown import Markdown

BACKEND_URL = os.getenv("ZENITH_BACKEND_URL", "http://127.0.0.1:8000")
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
VERSION = "8.2.0"
LOG_DIR = PROJECT_ROOT / "swarm_os" / "logs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
from swarm_os.services.control_plane.shared_model_registry import (
    LOCAL_MODEL_SPECS,
    CLOUD_MODEL_SPECS,
    ROLE_POOL,
)
from swarm_os.services.control_plane.bootstrap import build_router

from organism_console.state_store import SessionState
from organism_console.command_registry import registry, CommandContext
from organism_console.renderer import render_delegation_tree, render_step_micro_ui, render_trace_panel

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True), logging.FileHandler(LOG_DIR / "cli.log")]
)
log = logging.getLogger("zenith_cli")

SESSION_FILE = PROJECT_ROOT / "organism_console" / ".session.json"

class CLIContext(SessionState):
    def __init__(self):
        super().__init__(SESSION_FILE)
        self.console = Console(highlight=False)
        self.router = None

    def get_router(self):
        if self.router is None:
            self.router = build_router(include_cloud=self.cloud_enabled)
        return self.router

    def reset_router(self):
        self.router = None

    @property
    def selected_agent(self):
        return self.active_agent

    @selected_agent.setter
    def selected_agent(self, val):
        self.active_agent = val

    @property
    def model(self):
        return self.active_model

    @model.setter
    def model(self, val):
        self.active_model = val

ctx = CLIContext()

def run_async(coro):
    return asyncio.run(coro)

def get_system_stats():
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.05)
    return {
        "cpu": cpu,
        "ram_pct": ram.percent,
        "ram_used_gb": ram.used / (1024**3),
        "ram_total_gb": ram.total / (1024**3),
        "ram_color": "green" if ram.percent < 70 else "yellow" if ram.percent < 85 else "red"
    }

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def call_api(endpoint: str, method: str = "GET", payload: Any = None, stream: bool = False):
    try:
        url = f"{BACKEND_URL}{endpoint}"
        if method == "GET":
            return requests.get(url, timeout=10)
        if stream:
            return requests.post(url, json=payload, timeout=600, stream=True)
        return requests.post(url, json=payload, timeout=60)
    except requests.exceptions.RequestException as e:
        log.debug(f"API call failed: {e}")
        return None

def print_banner():
    stats = get_system_stats()
    backend_ok = call_api("/health") is not None
    backend_state = "[bold green]ONLINE[/bold green]" if backend_ok else "[bold red]OFFLINE[/bold red]"
    
    mode_style = "bold green" if ctx.mode == "safe" else "bold yellow"
    
    # Progress bar for RAM usage
    ram_pct = stats['ram_pct']
    bar_width = 10
    filled = int(ram_pct / 100 * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan")
    table.add_column()
    
    table.add_row("🤖 Active Agent", f"[cyan]{ctx.active_agent}[/cyan]")
    table.add_row("🧠 Active Model", f"[green]{ctx.active_model}[/green]")
    table.add_row("🛡️  System Mode", f"[{mode_style}]{ctx.mode.upper()}[/{mode_style}]")
    table.add_row("🔌 Backend API", backend_state)
    table.add_row("💻 System Load", f"CPU {stats['cpu']:.0f}% | RAM {stats['ram_used_gb']:.1f}GB/{stats['ram_total_gb']:.1f}GB [dim][{bar}][/dim]")
    
    banner_panel = Panel(
        table,
        title="[bold bright_white]⚡ ZENITH Swarm OS Control Terminal[/bold bright_white]",
        subtitle=f"[dim]v{VERSION}[/dim]",
        border_style="bold blue",
        expand=False,
        padding=(1, 4)
    )
    
    ctx.console.print()
    ctx.console.print(banner_panel)
    ctx.console.print()

def status_bar(agent, model, phase, ram_pct):
    stats = get_system_stats()
    phase_colors = {
        "thinking": "white",
        "planning": "yellow",
        "sensing": "cyan",
        "repair": "red",
        "swarm": "magenta",
        "resume": "blue",
        "ocular": "bright_cyan",
        "executing": "bright_green",
    }
    pc = phase_colors.get(phase, "white")
    return (
        f"[bold blue]topic[/bold blue]:[bright_white]{escape(ctx.current_topic)}[/bright_white] | "
        f"[bold blue]agent[/bold blue]:[cyan]{agent}[/cyan] | "
        f"[bold blue]model[/bold blue]:[green]{model}[/green] | "
        f"[bold blue]phase[/bold blue]:[{pc}]{phase}[/{pc}] | "
        f"[bold blue]ram[/bold blue]:[{stats['ram_color']}]{ram_pct:.0f}%[/{stats['ram_color']}]"
    )

def get_installed_models():
    try:
        resp = call_api("/status")
        if resp:
            return resp.json().get("installed_models", [])
    except Exception:
        pass
    return ["qwen2.5:7b-instruct"]

def stream_prompt(agent_id, prompt, history):
    stats = get_system_stats()
    if stats["ram_pct"] > 90:
        ctx.console.print("[bold red]⚠ SYSTEM PRESSURE:[/bold red] RAM critical, expect slower response.")

    payload = {
        "agent_id": agent_id,
        "prompt": prompt,
        "history": history,
        "focus_file": getattr(ctx, "focus_file", None),
    }
    resp = call_api(f"/agents/{agent_id}/step/stream", "POST", payload, stream=True)

    if not resp:
        ctx.console.print("[bold red]✗ ERROR:[/bold red] Backend unreachable.")
        return history

    full_content = ""
    model = "zenith-core"
    phase = "thinking"
    tool_calls = []
    handoffs_list = []
    start_time = time.time()

    ctx.console.print(Rule(style="dim blue"))

    # Reset delegation chain to current agent for new run
    ctx.delegation_chain = [agent_id]
    ctx.save()

    with Live(console=ctx.console, refresh_per_second=8, vertical_overflow="visible") as live:
        try:
            for line in resp.iter_lines():
                if not line:
                    continue

                chunk = json.loads(line)
                chunk_type = chunk.get("type")

                # Handle trace and step display
                if chunk_type == "model_plan":
                    requested_role = chunk.get("requested_role", "unknown")
                    chain = chunk.get("model_chain", []) or []
                    ctx.delegation_chain = chain if chain else [agent_id]
                    ctx.save()

                    if ctx.trace_mode:
                        live.stop()
                        panel = render_trace_panel(
                            "Router Decision & Path Planning",
                            {"requested_role": requested_role, "delegation_path": " -> ".join(ctx.delegation_chain)},
                            "cyan"
                        )
                        ctx.console.print(panel)
                        live.start()
                    else:
                        live.stop()
                        ctx.console.print(render_step_micro_ui("planning", f"Formulating plan for role: {requested_role}"))
                        live.start()
                    continue

                if chunk_type == "model_selected":
                    model = chunk.get("model", "unknown")
                    ctx.active_model = model
                    ctx.save()

                    if ctx.trace_mode:
                        live.stop()
                        panel = render_trace_panel(
                            "Model Selection",
                            {
                                "model": model,
                                "role": chunk.get("requested_role", "unknown"),
                                "attempt": chunk.get("attempt", 1),
                                "temperature": chunk.get("temperature", 0.7)
                            },
                            "green"
                        )
                        ctx.console.print(panel)
                        live.start()
                    else:
                        live.stop()
                        ctx.console.print(render_step_micro_ui("model_selected", f"selected {model}"))
                        live.start()
                    continue

                if chunk_type == "model_escalation":
                    from_model = chunk.get("from_model")
                    reason = chunk.get("reason")
                    if ctx.trace_mode:
                        live.stop()
                        panel = render_trace_panel(
                            "Model Escalation (Fallback)",
                            {"from_model": from_model, "escalated_reason": reason, "status": "switching to secondary/cloud"},
                            "red"
                        )
                        ctx.console.print(panel)
                        live.start()
                    else:
                        live.stop()
                        ctx.console.print(render_step_micro_ui("escalation", f"Escalated from {from_model} due to error: {reason}"))
                        live.start()
                    continue

                if chunk_type == "agent_handoff":
                    live.stop()
                    from_a = chunk.get("from", agent_id)
                    to_a = chunk.get("to", "executor")
                    task = str(chunk.get("task", ""))[:80]
                    handoffs_list.append({"from": from_a, "to": to_a, "task": task})
                    ctx.console.print(render_step_micro_ui("swarm", f"{from_a} → {to_a}: {task}"))
                    live.start()
                    continue

                if chunk_type == "tool_result":
                    tool = chunk.get("tool")
                    if ctx.trace_mode:
                        live.stop()
                        panel = render_trace_panel(
                            "Tool Execution Details",
                            {"tool": tool, "executing_model": chunk.get("model", "unknown")},
                            "yellow"
                        )
                        ctx.console.print(panel)
                        live.start()
                    else:
                        live.stop()
                        ctx.console.print(render_step_micro_ui("tool_call", f"executing tool {tool}"))
                        live.start()
                    continue

                if chunk_type == "final":
                    live.stop()
                    final_content = chunk.get("content", "")
                    ctx.console.print()
                    if isinstance(final_content, dict):
                        ctx.console.print(Panel(json.dumps(final_content, indent=2), border_style="green"))
                    elif final_content:
                        ctx.console.print(Panel(str(final_content), border_style="green"))
                    
                    new_history = list(history)
                    new_history.append({"role": "user", "content": prompt})
                    new_history.append({"role": "assistant", "content": final_content or full_content})
                    ctx.history = new_history
                    ctx.history_pointer = len(ctx.history) - 1
                    ctx.save()
                    return new_history

                if "ask_user" in chunk:
                    params = chunk["ask_user"]
                    question = params.get("question", "Input requested:")
                    options = params.get("options", [])

                    live.stop()
                    ctx.console.print()

                    if "APPROVAL REQUIRED" in question:
                        ctx.console.print(Panel(
                            Markdown(question),
                            title="🛡️  [bold yellow]Security Gate - Action Approval[/bold yellow]",
                            border_style="yellow",
                            padding=(1, 2)
                        ))
                    else:
                        ctx.console.print(Panel(
                            Markdown(question),
                            title="❓  [bold cyan]Agent Request[/bold cyan]",
                            border_style="cyan",
                            padding=(0, 1)
                        ))

                    if options:
                        from rich.prompt import Prompt
                        choices = []
                        for i, o in enumerate(options):
                            if isinstance(o, dict):
                                choices.append(str(o.get("label", o.get("value", i))))
                            else:
                                choices.append(str(o))
                        answer = Prompt.ask("[bold cyan]Choose option[/bold cyan]", choices=choices)
                    else:
                        answer = ctx.console.input("[bold cyan]Your response:[/bold cyan] ").strip()

                    new_history = list(history)
                    new_history.append({"role": "user", "content": prompt})
                    new_history.append({"role": "assistant", "content": full_content})
                    new_history.append({"role": "user", "content": f"Observation: {json.dumps({'answer': answer})}"})
                    return stream_prompt(agent_id, "", new_history)

                piece = chunk.get("content", "") or chunk.get("thinking", "")
                new_model = chunk.get("model")
                if new_model and new_model != model:
                    model = new_model
                    ctx.active_model = model
                    ctx.save()
                full_content += piece

                # Deduce execution phase
                if "<plan>" in full_content and "</plan>" not in full_content:
                    phase = "planning"
                elif "[Singularity:" in piece:
                    phase = "resume"
                elif "Observation:" in piece:
                    phase = "sensing"
                elif "[Self-Heal:" in piece:
                    phase = "repair"
                elif "<tool_call" in piece:
                    phase = "executing"

                if "<tool_call" in piece:
                    m = re.search(r'<tool_call name="([^"]+)">', piece)
                    if m:
                        tool_calls.append(m.group(1))

                topic_match = re.search(r'<topic_update title="(.*?)" summary="(.*?)"', full_content)
                if topic_match:
                    ctx.current_topic = topic_match.group(1)
                    ctx.current_summary = topic_match.group(2)

                intent_match = re.search(r'<strategic_intent>(.*?)</strategic_intent>', full_content, re.DOTALL)
                if intent_match:
                    ctx.strategic_intent = intent_match.group(1).strip()

                elapsed = time.time() - start_time
                stats = get_system_stats()

                # Clean display text from internal XML blocks
                display = re.sub(r"<plan>.*?(?:</plan>|$)", "", full_content, flags=re.DOTALL)
                display = re.sub(r"<strategic_intent>.*?(?:</strategic_intent>|$)", "", display, flags=re.DOTALL)
                display = re.sub(r"<topic_update.*?>", "", display)
                display = re.sub(r"<tool_call[^>]*>.*?(?:</tool_call>|$)", "", display, flags=re.DOTALL).strip()

                # Build live rendering block
                layout = Table.grid(padding=(0, 0))
                layout.add_column()
                layout.add_row(Text.from_markup(status_bar(agent_id, model, phase, stats["ram_pct"]) + f" [dim]{elapsed:.1f}s[/dim]"))

                if ctx.strategic_intent:
                    layout.add_row(Text.from_markup(f" [bold blue]intent[/bold blue]: [cyan]{ctx.strategic_intent}[/cyan]"))

                if ctx.current_topic != "Nexus Initialization":
                    layout.add_row(Panel(escape(ctx.current_summary), title=f"[bold bright_white]{escape(ctx.current_topic)}[/bold bright_white]", border_style="blue dim"))

                plan_match = re.search(r"<plan>(.*?)</plan>", full_content, re.DOTALL)
                if plan_match:
                    layout.add_row(Panel(plan_match.group(1).strip(), title="Plan", border_style="yellow dim"))

                if tool_calls:
                    layout.add_row(Text.from_markup(f"[dim]Tools:[/dim] {' '.join(f'[cyan]⚙ {t}[/cyan]' for t in tool_calls[-3:])}"))

                if display:
                    layout.add_row(Panel(display, border_style="bright_blue dim", padding=(0, 1)))

                # Construct Live Handoff Tracer Tree
                if ctx.delegation_chain:
                    tree_obj = Tree("[bold magenta]🐝 Swarm Handoff Trace[/bold magenta]")
                    curr_node = tree_obj
                    for i, agent in enumerate(ctx.delegation_chain):
                        task_desc = ""
                        if i > 0:
                            from_agent = ctx.delegation_chain[i-1]
                            to_agent = ctx.delegation_chain[i]
                            for h in handoffs_list:
                                if h["from"] == from_agent and h["to"] == to_agent:
                                    task_desc = f" [dim]({h['task']})[/dim]"
                                    break
                        if i == len(ctx.delegation_chain) - 1:
                            curr_node = curr_node.add(f"[bold green]▶ {agent}[/bold green] (active){task_desc}")
                        else:
                            curr_node = curr_node.add(f"[cyan]✓ {agent}[/cyan]{task_desc}")
                    layout.add_row(Panel(tree_obj, border_style="magenta dim", title="[bold magenta]Live Handoff Trace[/bold magenta]"))

                live.update(layout, refresh=True)

        except Exception as e:
            log.exception("Streaming exception")
            ctx.console.print(f"[bold red]Stream failed:[/bold red] {e}")

    ctx.console.print(Rule(style="dim blue"))

    # Estimate and update tokens
    input_text = prompt + json.dumps(history)
    input_tokens = estimate_tokens(input_text)
    output_tokens = estimate_tokens(full_content)
    ctx.total_input_tokens += input_tokens
    ctx.total_output_tokens += output_tokens
    ctx.save()

    new_history = list(history)
    new_history.append({"role": "user", "content": prompt})
    new_history.append({"role": "assistant", "content": full_content})
    ctx.history = new_history
    ctx.save()
    return new_history

def run_syntax_checks() -> tuple[bool, str]:
    import ast
    try:
        git_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=5
        )
        if git_diff.returncode == 0:
            modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
            for f in modified_files:
                file_path = PROJECT_ROOT / f
                if file_path.suffix == ".py" and file_path.exists():
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

def run_test_suite(goal_text: str = "") -> tuple[bool, str]:
    # 1. Detect if goal mentions a test file specifically
    test_targets = []
    m = re.search(r"tests/[a-zA-Z0-9_]+\.py", goal_text)
    if m:
        test_targets.append(m.group(0))
    
    # 2. If no target in goal, find modified files in git
    if not test_targets:
        try:
            git_diff = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=5
            )
            if git_diff.returncode == 0:
                modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
                for f in modified_files:
                    # If the file itself is a test, add it
                    if f.startswith("tests/") and f.endswith(".py"):
                        test_targets.append(f)
                    else:
                        # Find related tests by matching name substring
                        base = Path(f).stem
                        # Ignore common files or short names
                        if len(base) > 3 and base not in ("main", "__init__"):
                            # Search in tests folder
                            tests_dir = PROJECT_ROOT / "tests"
                            for t_file in tests_dir.glob("test_*.py"):
                                if base in t_file.name or t_file.name.replace("test_", "").replace(".py", "") in base:
                                    test_targets.append(f"tests/{t_file.name}")
        except Exception:
            pass

    # Deduplicate test targets
    test_targets = list(set(test_targets))

    cmd = [sys.executable, "-m", "pytest", "--tb=short"]
    if test_targets:
        cmd.extend(test_targets)
    else:
        # Fallback to run smoke tests only if no specific modifications
        cmd.append("tests/test_agents_smoke.py")
        cmd.append("tests/test_backend_smoke.py")
        
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=30
        )
        passed = result.returncode == 0
        return passed, result.stdout + "\n" + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out after 30 seconds."
    except Exception as e:
        return False, f"Failed to execute tests: {e}"

def draft_plan_first(goal: str, cmd_ctx: CommandContext) -> str:
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print("[dim]Drafting structured implementation plan...[/dim]")
    prompt = f"""
    You are an elite software architect. Create a structured markdown Implementation Plan for the objective: "{goal}".
    
    Structure your plan as follows:
    # Goal Description
    - Summary of changes
    ## Proposed Changes
    - Specify the exact files to modify and what changes to make in each.
    ## Verification Plan
    - Tests to run and manual verification steps.
    
    Return ONLY valid markdown text.
    """
    
    model = state.active_model or "qwen2.5:7b-instruct"
    try:
        resp = cmd_ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if resp and resp.status_code == 200:
            plan_text = resp.json().get("response", "").strip()
            return plan_text
    except Exception as e:
        console.print(f"[red]Error calling generator: {e}[/red]")
    return ""

def draft_task_list(plan_text: str, cmd_ctx: CommandContext) -> str:
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print("[dim]Drafting task checklist...[/dim]")
    prompt = f"""
    Based on the following Implementation Plan, generate a checklist of specific tasks.
    Each item must start with `- [ ]`.
    
    Plan:
    {plan_text}
    
    Return ONLY the list of items starting with `- [ ]`.
    """
    
    model = state.active_model or "qwen2.5:3b-instruct"
    try:
        resp = cmd_ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if resp and resp.status_code == 200:
            task_text = resp.json().get("response", "").strip()
            return task_text
    except Exception:
        pass
    return "- [ ] Implement proposed changes\n- [ ] Verify execution"

def run_autonomous_goal_loop(goal: str, cmd_ctx: CommandContext):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🤖 Swarm OS Autonomous Verification Loop[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    console.print(f"👥 [bold]Initial Agent[/bold]: [cyan]{state.active_agent}[/cyan]")
    console.print()
    
    from rich.prompt import Confirm
    plan_first = Confirm.ask("[bold yellow]Enable Plan-First Mode (generate plan & task list first)?[/bold yellow]")
    
    if plan_first:
        docs_dir = PROJECT_ROOT / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_file = docs_dir / "implementation_plan.md"
        task_file = docs_dir / "task.md"
        
        while True:
            plan_text = draft_plan_first(goal, cmd_ctx)
            if not plan_text:
                console.print("[red]Failed to generate plan. Falling back to immediate execution.[/red]")
                break
                
            console.print()
            console.print(Panel(plan_text, title="📋 [bold green]Implementation Plan Proposal[/bold green]", border_style="green"))
            console.print()
            
            if Confirm.ask("[bold cyan]Approve this plan and proceed to task creation?[/bold cyan]"):
                # Save the plan
                plan_file.write_text(plan_text, encoding="utf-8")
                console.print(f"[green]✓ Saved implementation plan to {plan_file}[/green]")
                
                # Generate task list
                task_text = draft_task_list(plan_text, cmd_ctx)
                task_file.write_text(task_text, encoding="utf-8")
                console.print(f"[green]✓ Saved task checklist to {task_file}[/green]")
                break
            else:
                refinement = console.input("[yellow]Provide feedback/refinements to regenerate the plan: [/yellow]").strip()
                goal = f"{goal} (Feedback: {refinement})"
    
    current_prompt = f"Goal: {goal}\n\nPlease audit, refactor, and fix the codebase to achieve this goal using your tools. Ensure syntax correctness and that all tests pass."
    
    history = list(ctx.history)
    max_attempts = 5
    
    for attempt in range(1, max_attempts + 1):
        console.print(Rule(f"Attempt {attempt}/{max_attempts}", style="magenta dim"))
        
        # 1. Run the agent stream
        history = stream_prompt(state.active_agent, current_prompt, history)
        
        # 1.5. Run fast syntax checks (Fail-fast optimization)
        console.print("[dim]Running fast syntax checks...[/dim]")
        syntax_passed, syntax_error_msg = run_syntax_checks()
        if not syntax_passed:
            passed = False
            logs = f"Syntax Error detected in modified files:\n\n{syntax_error_msg}"
            console.print("[bold red]✗ Fast Syntax Check Failed.[/bold red]")
        else:
            # 2. Run test verification
            console.print("[dim]Running test verification suite...[/dim]")
            passed, logs = run_test_suite(goal)
        
        if passed:
            console.print()
            console.print(Panel(
                "[bold green]✓ SUCCESS: Goal fully achieved and verified! All tests passed.[/bold green]",
                border_style="green"
            ))
            break
        else:
            # Extract traceback and key failures
            failures = []
            for line in logs.splitlines():
                if line.startswith("E   ") or "FAIL" in line or "AssertionError" in line or "Syntax Error" in line or "File:" in line or "Error:" in line:
                    failures.append(line)
            
            trace_preview = "\n".join(failures[:20])
            if not trace_preview:
                trace_preview = "\n".join(logs.splitlines()[-15:])
                
            console.print(f"[bold red]✗ Verification Failed on Attempt {attempt}.[/bold red]")
            if attempt == max_attempts:
                console.print(Panel(
                    f"[bold red]✗ FAILURE: Max attempts ({max_attempts}) reached. Tests/Checks are still failing.[/bold red]",
                    border_style="red"
                ))
                break
                
            # Formulate correction feedback
            console.print("[yellow]Feeding back failure logs to agent context for correction...[/yellow]")
            current_prompt = (
                f"The verification checks failed with the following traceback/logs:\n\n"
                f"```\n{trace_preview}\n```\n\n"
                f"Please analyze these errors, modify the code using your capabilities, and verify syntax to fix them."
            )

def run_debate_loop(goal: str, cmd_ctx: CommandContext):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🐝 Swarm OS Collaborative Agent Debate[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    console.print()
    
    # Phase 1: Planner Proposal
    console.print("[bold yellow]Phase 1: Planner Proposal[/bold yellow]")
    prompt1 = f"Please draft a detailed implementation proposal to achieve this goal: {goal}"
    stream_prompt("planner", prompt1, [])
    proposal = ctx.history[-1].get("content", "") if ctx.history else ""
    
    # Phase 2: Reviewer Critique
    console.print("\n[bold yellow]Phase 2: Reviewer Critique[/bold yellow]")
    prompt2 = f"Please audit the following implementation proposal for bugs, edge cases, and design flaws:\n\n{proposal}"
    stream_prompt("reviewer", prompt2, [])
    critique = ctx.history[-1].get("content", "") if ctx.history else ""
    
    # Phase 3: Coordinator Synthesis
    console.print("\n[bold yellow]Phase 3: Coordinator Synthesis[/bold yellow]")
    prompt3 = (
        f"You are the coordinator. Review the proposal and the critic's critique, resolve any disagreements, "
        f"and output a final corrected implementation plan.\n\n"
        f"PROPOSAL:\n{proposal}\n\n"
        f"CRITIQUE:\n{critique}"
    )
    stream_prompt("coordinator", prompt3, [])
    
    console.print()
    console.print(Panel(
        "[bold green]✓ Swarm debate complete. Final plan recorded in history. Run /plan show to inspect.[/bold green]",
        border_style="green"
    ))

def main():
    if len(sys.argv) > 1:
        # Run command directly and exit
        cmd_line = " ".join(sys.argv[1:])
        cmd_ctx = CommandContext(
            state=ctx,
            console=ctx.console,
            call_api=call_api,
            run_prompt=lambda p: stream_prompt(ctx.active_agent, p, ctx.history),
            get_system_stats=get_system_stats,
            installed_models=get_installed_models()
        )
        cmd_ctx.run_goal_loop = lambda g: run_autonomous_goal_loop(g, cmd_ctx)
        cmd_ctx.run_debate = lambda g: run_debate_loop(g, cmd_ctx)
        registry.handle_line(cmd_line, cmd_ctx)
        return 0

    print_banner()

    if not call_api("/health"):
        ctx.console.print("[bold red]✗ Backend appears offline.[/bold red] Use '/boot' (cmd line) or check logs.")

    while True:
        use_voice = False
        import ctypes
        winmm = None
        if getattr(ctx, "voice_mode", False):
            try:
                winmm = ctypes.windll.winmm
                if winmm.waveInGetNumDevs() > 0:
                    use_voice = True
            except Exception:
                pass

        if use_voice and winmm:
            try:
                winmm.mciSendStringW("open new type waveaudio alias recsound", None, 0, 0)
                winmm.mciSendStringW("record recsound", None, 0, 0)
            except Exception as e:
                log.debug(f"Failed to initiate background voice recording: {e}")
                use_voice = False

        try:
            if use_voice:
                prompt_str = f"[bold bright_white]zenith[/bold bright_white][blue]@[/blue][cyan]{ctx.active_agent}[/cyan] [bold blue]❯[/bold blue] [dim](🎙️  listening...)[/dim] "
            else:
                prompt_str = f"[bold bright_white]zenith[/bold bright_white][blue]@[/blue][cyan]{ctx.active_agent}[/cyan] [bold blue]❯[/bold blue] "
            
            cmd_line = ctx.console.input(prompt_str).strip()

            if use_voice and winmm:
                try:
                    winmm.mciSendStringW("stop recsound", None, 0, 0)
                except Exception:
                    pass

                if cmd_line:
                    try:
                        winmm.mciSendStringW("close recsound", None, 0, 0)
                    except Exception:
                        pass
                else:
                    try:
                        wav_path = str(PROJECT_ROOT / "dictation.wav")
                        save_cmd = f'save recsound "{wav_path}"'
                        winmm.mciSendStringW(save_cmd, None, 0, 0)
                        winmm.mciSendStringW("close recsound", None, 0, 0)
                        
                        from organism_console.command_registry import transcribe_wav
                        text = transcribe_wav(ctx.console, wav_path)
                        if text:
                            ctx.console.print(f"[bold yellow]Transcribed:[/bold yellow] [green]\"{text}\"[/green]")
                            cmd_line = text
                        else:
                            ctx.console.print("[yellow]No speech detected or transcription failed.[/yellow]")
                            continue
                    except Exception as e:
                        ctx.console.print(f"[bold red]Failed to save/transcribe voice input: {e}[/bold red]")
                        try:
                            winmm.mciSendStringW("close recsound", None, 0, 0)
                        except Exception:
                            pass
                        continue

            if not cmd_line:
                continue

            # Command Context for dispatching
            cmd_ctx = CommandContext(
                state=ctx,
                console=ctx.console,
                call_api=call_api,
                run_prompt=lambda p: stream_prompt(ctx.active_agent, p, ctx.history),
                get_system_stats=get_system_stats,
                installed_models=get_installed_models()
            )
            cmd_ctx.run_goal_loop = lambda g: run_autonomous_goal_loop(g, cmd_ctx)
            cmd_ctx.run_debate = lambda g: run_debate_loop(g, cmd_ctx)

            # Process command or prompt
            execute_prompt = registry.handle_line(cmd_line, cmd_ctx)
            if execute_prompt:
                if len(ctx.history) > 12:
                    ctx.console.print("[dim]⚡ [bold yellow]Context pressure warning:[/bold yellow] history exceeds 12 messages. Auto-compressing context in background...[/dim]")
                    registry.handle_line("/compress", cmd_ctx)
                ctx.history = stream_prompt(ctx.active_agent, execute_prompt, ctx.history)

        except KeyboardInterrupt:
            if getattr(ctx, "voice_mode", False):
                try:
                    import ctypes
                    winmm = ctypes.windll.winmm
                    winmm.mciSendStringW("stop recsound", None, 0, 0)
                    winmm.mciSendStringW("close recsound", None, 0, 0)
                except Exception:
                    pass
            ctx.console.print("\n[dim]Use '/exit' to quit properly.[/dim]")
            continue
        except EOFError:
            break
        except Exception as e:
            log.exception("Main loop exception")
            ctx.console.print(f"[bold red]Unexpected error:[/bold red] {e}")

    return 0


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
        f"[bold]Input tokens:[/bold]  {i:,}\n"
        f"[bold]Output tokens:[/bold] {o:,}\n"
        f"[bold]Total:[/bold]         {i+o:,}",
        title="[bold cyan]Token Usage[/bold cyan]",
        border_style="cyan"
    ))


if __name__ == "__main__":
    sys.exit(main())

