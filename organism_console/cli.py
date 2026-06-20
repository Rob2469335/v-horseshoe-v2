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
    backend_state = "[green]ONLINE[/green]" if backend_ok else "[red]OFFLINE[/red]"

    ctx.console.print()
    ctx.console.print(Rule(style="bold blue"))
    ctx.console.print(f" [bold bright_white]ZENITH Swarm Control Terminal[/bold bright_white] [dim]v{VERSION}[/dim]")
    ctx.console.print(f" [dim]System:[/dim] RAM {stats['ram_used_gb']:.1f}GB ({stats['ram_pct']:.0f}%) | CPU {stats['cpu']:.0f}% | [dim]Backend:[/dim] {backend_state}")
    ctx.console.print(Rule(style="bold blue"))
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
                    
                    # Record execution run to history
                    ctx.state.history.append({
                        "agent_id": agent_id,
                        "prompt": prompt,
                        "response": full_content,
                        "timestamp": time.time()
                    })
                    ctx.state.history_pointer = len(ctx.state.history) - 1
                    ctx.state.save()
                    return history

                if "ask_user" in chunk:
                    params = chunk["ask_user"]
                    question = params.get("question", "Input requested:")
                    options = params.get("options", [])

                    live.stop()
                    ctx.console.print()

                    if options:
                        from rich.prompt import Prompt
                        choices = []
                        for i, o in enumerate(options):
                            if isinstance(o, dict):
                                choices.append(str(o.get("label", o.get("value", i))))
                            else:
                                choices.append(str(o))
                        answer = Prompt.ask(f"[bold cyan]{question}[/bold cyan]", choices=choices)
                    else:
                        answer = ctx.console.input(f"[bold cyan]{question}[/bold cyan] ").strip()

                    new_history = list(history)
                    new_history.append({"role": "user", "content": prompt})
                    new_history.append({"role": "assistant", "content": full_content})
                    new_history.append({"role": "user", "content": f"Observation: {json.dumps({'answer': answer})}"})
                    return stream_prompt(agent_id, "", new_history)

                piece = chunk.get("content", "") or chunk.get("thinking", "")
                model = chunk.get("model", model)
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

                live.update(layout)

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

def run_test_suite(goal_text: str = "") -> tuple[bool, str]:
    # Detect if goal mentions a test file specifically
    test_target = None
    m = re.search(r"tests/[a-zA-Z0-9_]+\.py", goal_text)
    if m:
        test_target = m.group(0)
    
    cmd = [sys.executable, "-m", "pytest", "--tb=short"]
    if test_target:
        cmd.append(test_target)
        
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

def run_autonomous_goal_loop(goal: str, cmd_ctx: CommandContext):
    console = cmd_ctx.console
    state = cmd_ctx.state
    
    console.print()
    console.print(Rule("[bold magenta]🤖 Swarm OS Autonomous Verification Loop[/bold magenta]"))
    console.print(f"🎯 [bold]Goal[/bold]: [cyan]{goal}[/cyan]")
    console.print(f"👥 [bold]Initial Agent[/bold]: [cyan]{state.active_agent}[/cyan]")
    console.print()
    
    current_prompt = f"Goal: {goal}\n\nPlease audit, refactor, and fix the codebase to achieve this goal using your tools. Ensure syntax correctness and that all tests pass."
    
    history = list(ctx.history)
    max_attempts = 5
    
    for attempt in range(1, max_attempts + 1):
        console.print(Rule(f"Attempt {attempt}/{max_attempts}", style="magenta dim"))
        
        # 1. Run the agent stream
        history = stream_prompt(state.active_agent, current_prompt, history)
        
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
                if line.startswith("E   ") or "FAIL" in line or "AssertionError" in line:
                    failures.append(line)
            
            trace_preview = "\n".join(failures[:20])
            if not trace_preview:
                trace_preview = "\n".join(logs.splitlines()[-15:])
                
            console.print(f"[bold red]✗ Verification Failed on Attempt {attempt}.[/bold red]")
            if attempt == max_attempts:
                console.print(Panel(
                    f"[bold red]✗ FAILURE: Max attempts ({max_attempts}) reached. Tests are still failing.[/bold red]",
                    border_style="red"
                ))
                break
                
            # Formulate correction feedback
            console.print("[yellow]Feeding back traceback to agent context for correction...[/yellow]")
            current_prompt = (
                f"The test verification suite failed with the following traceback/logs:\n\n"
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
    proposal = state.history[-1]["response"] if state.history else ""
    
    # Phase 2: Reviewer Critique
    console.print("\n[bold yellow]Phase 2: Reviewer Critique[/bold yellow]")
    prompt2 = f"Please audit the following implementation proposal for bugs, edge cases, and design flaws:\n\n{proposal}"
    stream_prompt("reviewer", prompt2, [])
    critique = state.history[-1]["response"] if state.history else ""
    
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
        try:
            prompt_str = f"[bold bright_white]zenith[/bold bright_white][blue]@[/blue][cyan]{ctx.active_agent}[/cyan] [bold blue]❯[/bold blue] "
            cmd_line = ctx.console.input(prompt_str).strip()

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
                ctx.history = stream_prompt(ctx.active_agent, execute_prompt, ctx.history)

        except KeyboardInterrupt:
            ctx.console.print("\n[dim]Use '/exit' to quit properly.[/dim]")
            continue
        except EOFError:
            break
        except Exception as e:
            log.exception("Main loop exception")
            ctx.console.print(f"[bold red]Unexpected error:[/bold red] {e}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
