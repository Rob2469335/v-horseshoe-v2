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

@registry.register("learn", "Run the offline meta-learner to analyze history and generate system rules", aliases=["offline learn", "learn from history"])
def cmd_learn(cmd_ctx: CommandContext, args: List[str]) -> None:
    cmd_ctx.console.print("[cyan]Initializing Offline Meta-Learner...[/cyan]")
    cmd_ctx.console.print("[dim]This may take 1-2 minutes depending on LLM response times.[/dim]")
    
    import subprocess
    import sys
    from pathlib import Path
    
    script_path = Path("swarm_os/healing/offline_learner.py")
    if not script_path.exists():
        cmd_ctx.console.print("[red]Offline learner script not found.[/red]")
        return
        
    try:
        # Run it as a subprocess so we can stream the output in real time
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        stdout = process.stdout  # type: ignore[assignment]
        if stdout is None:
            cmd_ctx.console.print("[red]Failed to capture subprocess output[/red]")
            return
        for line in stdout:  # type: ignore[union-attr]
            if "[+]" in line or "[-]" in line:
                cmd_ctx.console.print(f"[dim]{line.strip()}[/dim]")
            elif "Rule:" in line or "-" in line[:3]:
                cmd_ctx.console.print(f"[green]{line.strip()}[/green]")
            else:
                cmd_ctx.console.print(line.strip())
                
        process.wait()
        if process.returncode == 0:
            cmd_ctx.console.print("\n[bold green]✓ System Rules successfully injected into semantic memory.[/bold green]")
        else:
            cmd_ctx.console.print("\n[bold red]✗ Offline learning pass encountered an error.[/bold red]")
    except Exception as e:
        cmd_ctx.console.print(f"[bold red]Failed to execute offline learner:[/bold red] {e}")


@registry.register("autofix", "Full auto-repair pipeline: diagnose, fix, validate, test, learn, and watch", aliases=["auto fix", "fix yourself", "heal bugs", "fix all", "repair all"])
def cmd_autofix(cmd_ctx: CommandContext, args: List[str]) -> None:
    import json
    import os
    from pathlib import Path
    from collections import Counter
    from rich.prompt import Confirm
    from rich.table import Table
    from organism_console.core.self_repair_engine import SelfRepairEngine
    from organism_console.core.repair_engine import (
        classify_failure, get_similar_lessons, load_cures,
        meta_classify_lessons, load_lessons, RepairWatchman,
    )

    limit = 5
    if args:
        try:
            limit = max(1, int(args[0]))
        except ValueError:
            pass

    event_file = "data/events/events.jsonl"
    if not os.path.exists(event_file):
        cmd_ctx.console.print("[red]No event logs found to analyze.[/red]")
        return

    cmd_ctx.console.print("[cyan]Analyzing historical event logs for recurring exceptions...[/cyan]")

    failures = []
    with open(event_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("event_type") == "tool_result":
                    res = data.get("payload", {}).get("result", {})
                    if not res.get("ok", False):
                        err = res.get("error", "").strip()
                        if err and len(err) < 500:
                            failures.append(err)
            except Exception:
                pass

    if not failures:
        cmd_ctx.console.print("[green]No historical failures found in logs![/green]")
        return

    counter = Counter(failures)
    top_errors = counter.most_common(limit)

    classified = []
    for err, count in top_errors:
        ftype, tier = classify_failure(err)
        cures = load_cures()
        has_cure = ftype in cures and any(
            any(kw in err.lower() for kw in cure.get("keywords", []))
            for cure in cures[ftype]
        )
        classified.append((err, count, ftype, tier, has_cure))

    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Count", style="bold yellow", justify="right")
    table.add_column("Error", style="white")
    table.add_column("Type", style="cyan")
    table.add_column("Known", style="green")
    for err, count, ftype, tier, has_cure in classified:
        known_icon = "[green]✓[/green]" if has_cure else "[dim]—[/dim]"
        table.add_row(str(count), err[:70], ftype, known_icon)
    cmd_ctx.console.print(Panel(table, title="[bold magenta]Top Recurring Bugs (Classified)[/bold magenta]", border_style="magenta"))

    tag = "dispatch" if "--dispatch" in args else None
    if tag is None:
        tag = "dispatch" if Confirm.ask("\n[bold yellow]Run full auto-repair pipeline?[/bold yellow]") else "skip"
    if tag == "skip":
        cmd_ctx.console.print("[yellow]Cancelled.[/yellow]")
        return

    engine = SelfRepairEngine(cmd_ctx)
    cmd_ctx.console.print("[bold cyan]Phase 1: Tiered repair (budget-ordered T0→T1→T2 with auto-validation)...[/bold cyan]")

    for err, count, ftype, tier, has_cure in classified:
        cmd_ctx.console.print(f"\n[bold]Error ({count}x):[/bold] {err[:100]}")
        similar = get_similar_lessons(err)
        if similar:
            cmd_ctx.console.print(f"[dim]↳ {len(similar)} similar past lessons found[/dim]")
        result = engine.diagnose_and_repair(err)
        if result.get("root_cause"):
            cmd_ctx.console.print(f"[dim]  Root cause: {result['root_cause'][:120]}[/dim]")
        if result.get("generated_test_file"):
            cmd_ctx.console.print(f"[dim]  Test saved: {result['generated_test_file']}[/dim]")

    stats = engine.show_stats()
    total = stats["total_repairs"]
    successes = stats["t0_hits"] + stats["t1_hits"] + stats["t2_hits"]
    failures = stats["failures"]

    # Phase 2: Meta-classification (learn from lesson patterns)
    cmd_ctx.console.print("\n[bold cyan]Phase 2: Meta-classification analysis...[/bold cyan]")
    lessons = load_lessons()
    analysis = meta_classify_lessons(lessons)
    if analysis.get("type_counts"):
        mtable = Table(box=SIMPLE, header_style="bold cyan")
        mtable.add_column("Type", style="bold yellow")
        mtable.add_column("Count", justify="right")
        mtable.add_column("Success Rate", style="green")
        for ftype, count in sorted(analysis["type_counts"].items(), key=lambda x: -x[1])[:8]:
            rate = analysis["success_rates"].get(ftype, 0)
            bar = "█" * int(rate * 10) + "░" * (10 - int(rate * 10))
            mtable.add_row(ftype, str(count), f"{bar} {rate:.0%}")
        cmd_ctx.console.print(Panel(mtable, title="[bold cyan]Taxonomy Health[/bold cyan]", border_style="cyan"))
        if analysis.get("low_success_types"):
            cmd_ctx.console.print(f"[yellow]Low success types (need retraining):[/yellow] {', '.join(analysis['low_success_types'])}")
        if analysis.get("orphan_error_tokens"):
            cmd_ctx.console.print(f"[dim]Unrecognized tokens: {analysis['orphan_error_tokens']}[/dim]")

    # Phase 3: Adversarial self-test
    cmd_ctx.console.print("\n[bold cyan]Phase 3: Adversarial self-test...[/bold cyan]")
    adv_results = engine.run_adversarial()
    cmd_ctx.console.print(f"  Detection: [green]{adv_results['detected']}[/green]/{adv_results['total']}  Fixed: [green]{adv_results['fixed']}[/green]/{adv_results['total']}")

    # Summary
    cmd_ctx.console.print(f"\n[bold cyan]Repair Summary:[/bold cyan] T0:[green]{stats['t0_hits']}[/green] T1:[green]{stats['t1_hits']}[/green] T2:[green]{stats['t2_hits']}[/green] Failed:[red]{failures}[/red] Tokens:{stats['tokens_spent']}")
    cures_count = sum(len(v) for v in load_cures().values())
    tests_dir = Path("swarm_os/healing/generated_tests")
    test_count = len(list(tests_dir.glob("*.py"))) if tests_dir.exists() else 0
    cmd_ctx.console.print(f"[dim]Knowledge base: {cures_count} distilled cures | {len(lessons)} historical lessons | {test_count} generated tests[/dim]")

    # Phase 4: Watchman (proactive background monitor)
    if successes > failures and Confirm.ask("\n[bold yellow]Start proactive watchman daemon to auto-repair future errors?[/bold yellow]"):
        _WATCHMAN_INSTANCES = getattr(cmd_ctx, "_watchman_instances", {})
        if not _WATCHMAN_INSTANCES.get("default") or not _WATCHMAN_INSTANCES["default"].is_running:
            watchman = RepairWatchman(engine, interval_seconds=30)
            watchman.start()
            _WATCHMAN_INSTANCES["default"] = watchman
            setattr(cmd_ctx, "_watchman_instances", _WATCHMAN_INSTANCES)
            cmd_ctx.console.print("[green]✓ Watchman daemon started (monitoring events.jsonl)[/green]")

    # Phase 5: Autonomous agent fallback for unfixed
    if failures > 0 and Confirm.ask("\n[bold yellow]Dispatch autonomous agent for remaining unfixed bugs?[/bold yellow]"):
        unfixed = [err for err, _count, _ftype, _tier, _ in classified if err]
        instruction = (
            "I am the Swarm OS self-repair engine. I have identified the following recurring exceptions "
            "and bugs. Please search the codebase, find where these errors "
            "originate from, and write the code to fix them permanently.\n\n"
            + "\n".join(f"- ({count}x) {err}" for err, count in counter.most_common(limit))
        )
        try:
            if cmd_ctx.run_goal_loop:
                cmd_ctx.run_goal_loop(instruction)
            else:
                from organism_console.loops.autonomous import run_autonomous_goal_loop
                run_autonomous_goal_loop(instruction, cmd_ctx)
            cmd_ctx.console.print("[bold green]✓ Autonomous fix dispatch complete.[/bold green]")
        except Exception as e:
            cmd_ctx.console.print(f"[bold red]Autonomous fix failed:[/bold red] {e}")

    cmd_ctx.console.print("[bold green]✓ Auto-repair pipeline complete.[/bold green]")


@registry.register("cures", "Show distilled repair knowledge from past fixes")
def cmd_cures(cmd_ctx: CommandContext, args: List[str]) -> None:
    from rich.table import Table
    from organism_console.core.repair_engine import load_cures
    cures = load_cures()
    if not cures:
        cmd_ctx.console.print("[dim]No distilled cures yet. Run /autofix to build knowledge.[/dim]")
        return
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Failure Type", style="bold yellow")
    table.add_column("Cures", style="white")
    table.add_column("Top Action", style="green")
    for ftype, entries in sorted(cures.items()):
        top = max(entries, key=lambda x: x.get("count", 0)) if entries else {}
        action = top.get("action", "")[:60]
        table.add_row(ftype, str(len(entries)), action)
    cmd_ctx.console.print(Panel(table, title="[bold cyan]Distilled Repair Cures[/bold cyan]", border_style="cyan"))


@registry.register("repair-stats", "Show self-repair engine performance statistics")
def cmd_repair_stats(cmd_ctx: CommandContext, args: List[str]) -> None:
    from rich.table import Table
    from organism_console.core.self_repair_engine import SelfRepairEngine
    engine = SelfRepairEngine()
    stats = engine.show_stats()
    lessons = engine.show_lessons()
    
    table = Table(box=SIMPLE, header_style="bold cyan")
    table.add_column("Metric", style="bold yellow")
    table.add_column("Value", style="white")
    table.add_row("T0 (Pattern Repairs)", str(stats["t0_hits"]))
    table.add_row("T1 (Constrained Repairs)", str(stats["t1_hits"]))
    table.add_row("T2 (Deep Regeneration)", str(stats["t2_hits"]))
    table.add_row("Failed Repairs", str(stats["failures"]))
    table.add_row("Total Repairs", str(stats["total_repairs"]))
    table.add_row("Tokens Spent", str(stats["tokens_spent"]))
    table.add_row("Total Lessons", str(len(lessons)))
    cmd_ctx.console.print(Panel(table, title="[bold cyan]Self-Repair Engine Stats[/bold cyan]", border_style="cyan"))


@registry.register("graphrag", "Query GraphRAG community summaries directly", aliases=["gr", "graph"])
def cmd_graphrag(cmd_ctx: CommandContext, args: List[str]) -> None:
    if not args:
        cmd_ctx.console.print("[yellow]Usage: /graphrag <query>[/yellow]")
        return
    query = " ".join(args)
    cmd_ctx.console.print(f"[cyan]Querying GraphRAG for:[/cyan] {query}")
    
    resp = cmd_ctx.call_api("/memory/graphrag", "POST", {"query": query})
    if resp and resp.status_code == 200:
        data = resp.json()
        from rich.console import Group
        from rich.tree import Tree
        from rich.markdown import Markdown
        
        answer_renderable = Panel(Markdown(data.get("answer", "No answer found.")), title="GraphRAG Answer", border_style="green")
        
        tree = Tree("🔍 Detected Communities")
        communities = data.get("communities", {})
        for comm_name, nodes in communities.items():
            branch = tree.add(f"[cyan]{comm_name}[/cyan]")
            for node in nodes:
                branch.add(f"[dim]{node}[/dim]")
                
        cmd_ctx.console.print(Group(answer_renderable, tree))
    else:
        cmd_ctx.console.print("[red]Failed to fetch GraphRAG summary from backend.[/red]")


@registry.register("sandbox", "Execute a command in the local MCP sandbox", aliases=["sh", "exec"])
def cmd_sandbox(cmd_ctx: CommandContext, args: List[str]) -> None:
    if not args:
        cmd_ctx.console.print("[yellow]Usage: /sandbox <command>[/yellow]")
        return
    command = " ".join(args)
    cmd_ctx.console.print(f"[dim]Executing in sandbox:[/dim] {command}")
    
    resp = cmd_ctx.call_api("/tools/execute", "POST", {"tool": "terminal_exec", "arguments": {"command": command}})
    if resp and resp.status_code == 200:
        data = resp.json()
        output = data.get("result", "")
        cmd_ctx.console.print(Panel(output, title=f"Sandbox: {command}", border_style="yellow"))
    else:
        cmd_ctx.console.print("[red]Sandbox execution failed.[/red]")
