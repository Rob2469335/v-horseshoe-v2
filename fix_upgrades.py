import pathlib, os

CLI = pathlib.Path(r"C:\Users\rober\Projects\v-horseshoe-v2\organism_console\cli.py")
SVC = pathlib.Path(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py")
LOG = r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\logs"

src = CLI.read_text(encoding="utf-8")

src = src.replace(
    '        ("exit", "Exit console"),\n        ("<anything else>", "Send to active agent"),',
    '        ("exit", "Exit console"),\n        ("history", "Show conversation history"),\n        ("retry", "Resend last prompt"),\n        ("watch", "Live system dashboard (Ctrl+C to exit)"),\n        ("events", "Show recent organism events"),\n        ("snapshot [file]", "Save session to file"),\n        ("load [file]", "Restore session from file"),\n        ("model <name>", "Switch model mid-session"),\n        ("temp <0.0-1.0>", "Adjust temperature"),\n        ("agent <name> <prompt>", "One-shot any agent"),\n        ("<anything else>", "Send to active agent"),'
)

insert = r'''
def cmd_history(history):
    if not history:
        console.print("[dim]No history yet[/dim]")
        return
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")[:180]
        console.print(f"  [cyan]{role}:[/cyan] {content}")

def cmd_watch():
    import time as _t
    console.print("[dim]Live watch - Ctrl+C to stop[/dim]")
    try:
        with Live(console=console, refresh_per_second=2) as live:
            while True:
                cpu, ram_pct, ram_used, ram_total = get_resources()
                rc = ram_color(ram_pct)
                resp = call_backend("/status")
                d = resp.json() if resp else {}
                t = Table(border_style="dim", box=ROUNDED, show_header=False)
                t.add_column("", style="dim", width=16)
                t.add_column("", style="bold")
                t.add_row("RAM", f"[{rc}]{ram_used}/{ram_total}GB ({ram_pct:.0f}%)[/{rc}]")
                t.add_row("CPU", f"{cpu:.0f}%")
                t.add_row("Ollama", "[green]online[/green]" if d.get("ollama_reachable") else "[red]offline[/red]")
                t.add_row("Models", str(d.get("installed_model_count", 0)))
                t.add_row("Events", str(d.get("event_count", 0)))
                t.add_row("Time", datetime.now().strftime("%H:%M:%S"))
                live.update(Panel(t, title="[bold bright_cyan]ZENITH WATCH[/bold bright_cyan]", border_style="bright_cyan"))
                _t.sleep(0.5)
    except KeyboardInterrupt:
        console.print("[dim]Watch stopped[/dim]")

def cmd_events():
    resp = call_backend("/events?limit=20")
    if not resp:
        console.print("[red]Backend offline[/red]")
        return
    events = resp.json().get("events", [])
    if not events:
        console.print("[dim]No events yet[/dim]")
        return
    for ev in events[-10:]:
        ts = str(ev.get("occurred_at", ev.get("timestamp", "")))[:19]
        etype = ev.get("event_type", ev.get("event", "unknown"))
        source = ev.get("source", ev.get("id", ""))
        console.print(f"  [dim]{ts}[/dim]  [cyan]{etype}[/cyan]  [dim]{source}[/dim]")

def cmd_snapshot(history, filename=None):
    import json as _j, pathlib as _pl, datetime as _dt
    if not history:
        console.print("[dim]Nothing to snapshot[/dim]")
        return
    fn = filename or ("zenith_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    path = _pl.Path(r"''' + LOG + r'''") / fn
    path.parent.mkdir(exist_ok=True)
    path.write_text(_j.dumps({"history": history, "saved_at": str(_dt.datetime.now())}, indent=2), encoding="utf-8")
    console.print(f"[green]Saved[/green] {path}")

def cmd_load(filename):
    import json as _j, pathlib as _pl
    log_path = _pl.Path(r"''' + LOG + r'''")
    if not filename:
        snaps = sorted(log_path.glob("zenith_*.json"), reverse=True)[:5]
        if not snaps:
            console.print("[dim]No snapshots[/dim]")
            return None
        for i, s in enumerate(snaps, 1):
            console.print(f"  [dim]{i}.[/dim] {s.name}")
        return None
    path = log_path / filename
    if not path.exists():
        console.print(f"[red]Not found: {filename}[/red]")
        return None
    data = _j.loads(path.read_text(encoding="utf-8"))
    h = data.get("history", [])
    console.print(f"[green]Loaded[/green] {len(h)} messages")
    return h
'''

src = src.replace("def main():", insert + "\n\ndef main():")

src = src.replace(
'''            elif cmd == "run":
                cmd_run(args)''',
'''            elif cmd == "run":
                cmd_run(args)
            elif cmd == "history":
                cmd_history(history)
            elif cmd == "retry":
                last = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
                if last:
                    history = history[:-2] if len(history) >= 2 else []
                    history = stream_prompt(selected_agent, last, history)
                else:
                    console.print("[dim]Nothing to retry[/dim]")
            elif cmd == "watch":
                cmd_watch()
            elif cmd == "events":
                cmd_events()
            elif cmd == "snapshot":
                cmd_snapshot(history, args or None)
            elif cmd == "load":
                loaded = cmd_load(args)
                if loaded is not None:
                    history = loaded
            elif cmd == "model":
                if args:
                    os.environ["ZENITH_MODEL"] = args.strip()
                    console.print(f"[green]Model[/green] -> [bold]{args.strip()}[/bold]")
                else:
                    console.print(f"[dim]{os.environ.get('ZENITH_MODEL','qwen2.5:7b-instruct')}[/dim]")
            elif cmd == "temp":
                try:
                    os.environ["ZENITH_TEMP"] = str(float(args))
                    console.print(f"[green]Temp[/green] -> [bold]{args}[/bold]")
                except ValueError:
                    console.print("[dim]Usage: temp <0.0-1.0>[/dim]")
            elif cmd == "agent":
                ap = args.split(" ", 1)
                if len(ap) == 2:
                    stream_prompt(ap[0], ap[1], [])
                else:
                    console.print("[dim]Usage: agent <name> <prompt>[/dim]")'''
)

SVC_SRC = SVC.read_text(encoding="utf-8")
SVC_SRC = SVC_SRC.replace(
    '            chosen_model = "qwen2.5:7b-instruct" if _agent_role == "reasoning" else "qwen2.5:3b-instruct"',
    '''            import os as _os
            _env_model = _os.environ.get("ZENITH_MODEL", "")
            chosen_model = _env_model if _env_model else ("qwen2.5:7b-instruct" if _agent_role == "reasoning" else "qwen2.5:3b-instruct")
            _temp = float(_os.environ.get("ZENITH_TEMP", "0.7"))'''
)
SVC_SRC = SVC_SRC.replace(
    'json={"model": chosen_model, "messages": messages, "stream": True}',
    'json={"model": chosen_model, "messages": messages, "stream": True, "options": {"temperature": _temp}}'
)

CLI.write_text(src, encoding="utf-8")
SVC.write_text(SVC_SRC, encoding="utf-8")
print("done")
