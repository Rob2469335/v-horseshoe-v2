import sys
import requests
import json
import time
import subprocess
import os
import re
from datetime import datetime
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED, SIMPLE
from rich.rule import Rule
from rich.columns import Columns
from rich.align import Align
from rich.spinner import Spinner
from rich.padding import Padding
import psutil

BACKEND_URL = "http://127.0.0.1:8000"
PROJECT_ROOT = r"C:\Users\rober\Projects\v-horseshoe-v2"
VERSION = "8.0.0"
console = Console(highlight=False)

# ── Resources ──────────────────────────────────────────────────────────────

def get_resources():
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.1)
    return cpu, ram.percent, ram.used // (1024**3), ram.total // (1024**3)

def ram_color(pct):
    return "green" if pct < 70 else "yellow" if pct < 85 else "red"

# ── Banner ─────────────────────────────────────────────────────────────────

def print_banner():
    cpu, ram_pct, ram_used, ram_total = get_resources()
    rc = ram_color(ram_pct)
    backend_ok = call_backend("/health") is not None
    backend_str = "[green]online[/green]" if backend_ok else "[red]offline[/red]"
    console.print()
    console.print(Rule(f"[bold bright_cyan]ZENITH[/bold bright_cyan]  [dim]Swarm OS v{VERSION}[/dim]", style="bright_cyan"))
    console.print(
        f"  [{rc}]RAM {ram_used}/{ram_total}GB ({ram_pct:.0f}%)[/{rc}]"
        f"  [dim]CPU {cpu:.0f}%[/dim]"
        f"  Backend {backend_str}"
    )
    console.print()

# ── Status bar ─────────────────────────────────────────────────────────────

def status_bar(agent, model, phase, ram_pct):
    rc = ram_color(ram_pct)
    phase_colors = {
        "thinking": "white", "planning": "yellow", "sensing": "cyan",
        "repair": "red", "swarm": "magenta", "resume": "blue", "ocular": "bright_cyan"
    }
    pc = phase_colors.get(phase, "white")
    return (
        f"[dim]agent:[/dim] [bold green]{agent}[/bold green]  "
        f"[dim]model:[/dim] [bold yellow]{model}[/bold yellow]  "
        f"[dim]phase:[/dim] [{pc}]{phase}[/{pc}]  "
        f"[dim]ram:[/dim] [{rc}]{ram_pct:.0f}%[/{rc}]"
    )

# ── Stream prompt ──────────────────────────────────────────────────────────

def stream_prompt(agent_id, prompt, history):
    _, ram_pct, _, _ = get_resources()
    if ram_pct > 92:
        console.print(f"[bold red]⚠ RAM critical ({ram_pct:.0f}%) — using smallest model[/bold red]")

    payload = {"agent_id": agent_id, "prompt": prompt, "history": history}
    resp = call_backend("/agents/step/stream", "POST", payload, stream=True)

    if not resp:
        console.print("[red]✗ Backend unreachable[/red]")
        return history

    full_content = ""
    model = "zenith"
    phase = "thinking"
    tool_calls_made = []
    start = time.time()

    console.print(Rule(style="dim white"))

    with Live(console=console, refresh_per_second=4, vertical_overflow="visible") as live:
        try:
            buffer = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue

                if "error" in chunk:
                    live.stop()
                    console.print(f"[red]✗ {chunk['error']}[/red]")
                    break

                piece = chunk.get("content", "")
                model = chunk.get("model", model)
                full_content += piece
                buffer += piece

                # Detect phase
                if "<plan>" in full_content and "</plan>" not in full_content:
                    phase = "planning"
                elif "[Singularity:" in piece:
                    phase = "resume"
                elif "[Visual Telemetry:" in piece:
                    phase = "ocular"
                elif "[Self-Heal:" in piece:
                    phase = "repair"
                elif "[Swarm: Spawning" in piece:
                    phase = "swarm"
                elif "Observation:" in piece:
                    phase = "sensing"
                elif "<tool_call" in piece:
                    phase = "thinking"
                    # Extract tool name for indicator
                    m = re.search(r'<tool_call name="([^"]+)">', piece)
                    if m:
                        tool_calls_made.append(m.group(1))

                # Only redraw every ~120 chars to avoid spam
                if len(buffer) < 120 and not chunk.get("done"):
                    continue
                buffer = ""

                _, ram_pct, _, _ = get_resources()
                elapsed = time.time() - start

                # Build display
                display = re.sub(r'<plan>.*?</plan>', '', full_content, flags=re.DOTALL)
                display = re.sub(r'<tool_call[^>]*>.*?</tool_call>', '', display, flags=re.DOTALL)
                display = display.strip()

                layout = Table.grid(padding=(0, 0))
                layout.add_column()

                # Status line
                layout.add_row(Text.from_markup(
                    status_bar(agent_id, model, phase, ram_pct) +
                    f"  [dim]{elapsed:.1f}s[/dim]"
                ))
                layout.add_row(Text(""))

                # Plan block if present
                plan_match = re.search(r'<plan>(.*?)</plan>', full_content, re.DOTALL)
                if plan_match:
                    layout.add_row(Panel(
                        Text(plan_match.group(1).strip(), style="italic yellow"),
                        title="[bold yellow]Plan[/bold yellow]",
                        border_style="yellow dim",
                        padding=(0, 1)
                    ))
                    layout.add_row(Text(""))

                # Tool calls made
                if tool_calls_made:
                    tools_text = "  ".join(f"[cyan]⚙ {t}[/cyan]" for t in tool_calls_made[-3:])
                    layout.add_row(Text.from_markup(f"[dim]Tools:[/dim] {tools_text}"))
                    layout.add_row(Text(""))

                # Main content
                if display:
                    layout.add_row(Panel(
                        Markdown(display),
                        border_style="bright_cyan dim",
                        padding=(0, 1)
                    ))

                live.update(layout)

        except Exception as e:
            live.stop()
            console.print(f"[red]Stream error: {e}[/red]")

    console.print()
    if tool_calls_made:
        console.print(f"[dim]Tools used: {', '.join(tool_calls_made)}[/dim]")
    console.print()

    new_history = list(history)
    new_history.append({"role": "user", "content": prompt})
    new_history.append({"role": "assistant", "content": full_content})
    return new_history

# ── Backend ────────────────────────────────────────────────────────────────

def call_backend(endpoint, method="GET", payload=None, stream=False):
    try:
        url = f"{BACKEND_URL}{endpoint}"
        if method == "GET":
            return requests.get(url, timeout=60)
        if stream:
            return requests.post(url, json=payload, timeout=600, stream=True)
        return requests.post(url, json=payload, timeout=120)
    except Exception:
        return None

# ── Commands ───────────────────────────────────────────────────────────────

def cmd_resources():
    cpu, ram_pct, ram_used, ram_total = get_resources()
    rc = ram_color(ram_pct)
    table = Table(border_style="dim", box=ROUNDED, show_header=False)
    table.add_column("Metric", style="dim")
    table.add_column("Value", style="bold")
    table.add_row("RAM", f"[{rc}]{ram_used}/{ram_total} GB ({ram_pct:.0f}%)[/{rc}]")
    table.add_row("CPU", f"{'[red]' if cpu > 80 else '[green]'}{cpu:.0f}%[/]")
    table.add_row("Backend", "[green]online[/green]" if call_backend("/health") else "[red]offline[/red]")
    try:
        models_resp = call_backend("/status")
        if models_resp:
            d = models_resp.json()
            ollama_status = "[green]reachable[/green]" if d.get("ollama_reachable") else "[red]unreachable[/red]"
            table.add_row("Ollama", ollama_status)
            table.add_row("Models", str(d.get("installed_model_count", 0)))
    except Exception:
        pass
    console.print(Panel(table, title="[bold]System[/bold]", border_style="dim"))

def cmd_models():
    resp = call_backend("/status")
    if not resp:
        console.print("[red]Backend offline[/red]")
        return
    d = resp.json()
    models = d.get("installed_models", [])
    if not models:
        console.print("[dim]No models found[/dim]")
        return
    table = Table(border_style="dim", box=SIMPLE)
    table.add_column("Model", style="bold cyan")
    table.add_column("Type", style="dim")
    for m in models:
        mtype = "vision" if any(x in m for x in ["vl","vision","moondream"]) else \
                "embed" if any(x in m for x in ["embed","nomic"]) else \
                "coder" if "coder" in m else "chat"
        table.add_row(m, mtype)
    console.print(Panel(table, title="[bold]Installed Models[/bold]", border_style="dim"))

def cmd_run(code):
    if not code:
        console.print("[dim]Usage: run <python code>[/dim]")
        return
    resp = call_backend("/tools/sandbox", "POST", {"code": code})
    if not resp:
        console.print("[red]Sandbox unreachable[/red]")
        return
    result = resp.json()
    if result.get("stdout"):
        console.print(Panel(result["stdout"], title="[green]Output[/green]", border_style="green"))
    if result.get("stderr"):
        console.print(Panel(result["stderr"], title="[red]Error[/red]", border_style="red"))

def cmd_vault(args):
    parts = args.split(" ", 1)
    op = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    if op in ("", "list"):
        resp = call_backend("/vault")
        if resp:
            rules = resp.json().get("rules", [])
            if not rules:
                console.print("[dim]Vault empty — add rules with: vault add <rule>[/dim]")
                return
            for i, rule in enumerate(rules, 1):
                console.print(f"  [dim]{i}.[/dim] {rule}")
    elif op == "add" and rest:
        resp = call_backend("/vault", "POST", {"action": "add", "rule": rest})
        if resp:
            console.print(f"[green]✓[/green] Added: {rest}")
    elif op == "remove" and rest:
        try:
            resp = call_backend("/vault", "POST", {"action": "remove", "index": int(rest)-1})
            if resp:
                console.print(f"[green]✓[/green] Removed rule {rest}")
        except ValueError:
            console.print("[dim]Usage: vault remove <number>[/dim]")
    else:
        console.print("[dim]vault list | vault add <rule> | vault remove <n>[/dim]")

def cmd_status():
    resp = call_backend("/status")
    if not resp:
        console.print("[red]Backend offline[/red]")
        return
    d = resp.json()
    table = Table(border_style="dim", box=SIMPLE, show_header=False)
    table.add_column("Key", style="dim")
    table.add_column("Value", style="bold")
    table.add_row("Status", "[green]ok[/green]" if d.get("status") == "ok" else "[red]degraded[/red]")
    table.add_row("Ollama", "[green]✓[/green]" if d.get("ollama_reachable") else "[red]✗[/red]")
    table.add_row("Models", str(d.get("installed_model_count", 0)))
    table.add_row("Events", str(d.get("event_count", 0)))
    table.add_row("Env", d.get("environment", "unknown"))
    console.print(Panel(table, title="[bold]Backend Status[/bold]", border_style="dim"))

def cmd_heal():
    console.print("[dim]Running heal cycle...[/dim]")
    resp = call_backend("/api/admin/healing/run", "POST")
    if not resp:
        console.print("[red]Heal endpoint unreachable[/red]")
        return
    d = resp.json()
    color = "green" if d.get("last_heal_success") else "red"
    console.print(f"[{color}]Readiness: {d.get('recovery_readiness', 0)}%  Anomalies: {d.get('active_anomalies', 0)}[/{color}]")
    checks = d.get("checks", {})
    for name, result in checks.items():
        icon = "[green]✓[/green]" if result.get("ok") else "[red]✗[/red]"
        console.print(f"  {icon} {name}")

def cmd_traces():
    resp = call_backend("/traces?limit=10")
    if not resp:
        console.print("[red]Backend offline[/red]")
        return
    d = resp.json()
    traces = d.get("traces", [])
    if not traces:
        console.print("[dim]No traces yet[/dim]")
        return
    for t in traces[-5:]:
        console.print(f"  [dim]{t.get('bucket', '')}[/dim]  events:[cyan]{t.get('event_count',0)}[/cyan]  ok:[green]{t.get('success_count',0)}[/green]  fail:[red]{t.get('fail_count',0)}[/red]")

def cmd_help():
    table = Table(border_style="dim", box=SIMPLE)
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Description", style="white")
    rows = [
        ("status", "Backend + Ollama health"),
        ("resources", "RAM, CPU, system info"),
        ("models", "List installed Ollama models"),
        ("heal", "Run self-heal cycle"),
        ("traces", "Recent event traces"),
        ("run <code>", "Execute Python in sandbox"),
        ("vault list", "Show Architect's Vault rules"),
        ("vault add <rule>", "Add a rule"),
        ("vault remove <n>", "Remove rule by number"),
        ("select <agent>", "Switch agent (coordinator/planner/executor)"),
        ("list", "List all agents"),
        ("clear", "Clear session memory"),
        ("boot", "Restart Zenith kernel"),
        ("exit", "Exit console"),
        ("<anything else>", "Send to active agent"),
    ]
    for cmd, desc in rows:
        table.add_row(cmd, desc)
    console.print(Panel(table, title="[bold]Commands[/bold]", border_style="dim cyan"))

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    history = []
    selected_agent = "coordinator"

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        stream_prompt("coordinator", task, [])
        return 0

    print_banner()

    resp = call_backend("/health")
    if not resp or resp.status_code != 200:
        console.print("[red]✗ Backend offline — type 'boot' to start[/red]\n")
    else:
        console.print("[green]✓ Backend online[/green]\n")

    while True:
        try:
            prompt_str = f"[bold green]zenith[/bold green][dim white]@[/dim white][bold cyan]{selected_agent}[/bold cyan] [dim yellow]›[/dim yellow] "
            cmd_line = console.input(prompt_str).strip()
            if not cmd_line:
                continue
            if cmd_line.lower() in ("exit", "quit"):
                console.print("[dim]Goodbye.[/dim]")
                break

            parts = cmd_line.split(" ", 1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd == "help":
                cmd_help()
            elif cmd == "status":
                cmd_status()
            elif cmd == "resources":
                cmd_resources()
            elif cmd == "models":
                cmd_models()
            elif cmd == "heal":
                cmd_heal()
            elif cmd == "traces":
                cmd_traces()
            elif cmd == "boot":
                subprocess.Popen("powershell.exe -File start-dev.ps1", shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
                console.print("[cyan]Booting...[/cyan]")
            elif cmd == "clear":
                history = []
                console.print("[dim]Session cleared[/dim]")
            elif cmd == "status":
                cmd_status()
            elif cmd == "resources":
                cmd_resources()
            elif cmd == "models":
                cmd_models()
            elif cmd == "heal":
                cmd_heal()
            elif cmd == "traces":
                cmd_traces()
            elif cmd == "run":
                cmd_run(args)
            elif cmd == "vault":
                cmd_vault(args)
            elif cmd == "select":
                selected_agent = args.strip() or "coordinator"
                console.print(f"[dim]Agent → [bold]{selected_agent}[/bold][/dim]")
            elif cmd == "list":
                resp = call_backend("/agents")
                if resp:
                    for a in resp.json():
                        console.print(f"  [cyan]{a['id']}[/cyan]  [dim]{a.get('role','')}[/dim]")
            elif cmd == "help":
                cmd_help()
            else:
                history = stream_prompt(selected_agent, cmd_line, history)

        except KeyboardInterrupt:
            console.print()
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    return 0

if __name__ == "__main__":
    sys.exit(main())
