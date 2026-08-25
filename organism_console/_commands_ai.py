from swarm_os.core.settings import get_settings
_s = get_settings()
_qdrant_url = _s.qdrant_url
_emb_url = f'http://{_s.host}:{_s.port}/v1/embeddings'
"""AI, memory, and maintenance CLI commands."""

import concurrent.futures
import json
import re
import sys
from pathlib import Path
from typing import List

from rich.panel import Panel
from rich.table import Table
from rich.box import SIMPLE
from rich.markup import escape
from rich.markdown import Markdown

from organism_console.command_registry import registry
from organism_console._command_context import CommandContext


@registry.register(
    "memory",
    "Query or inject memory into Qdrant store. Usage: /memory query|inject <value>",
)
def cmd_memory(ctx: CommandContext, args: List[str]) -> None:
    from datetime import datetime, timezone

    if len(args) < 2:
        ctx.console.print(
            "[yellow]Usage: /memory query <term> OR /memory inject <text>[/yellow]"
        )
        return
    action = args[0].lower()
    text = " ".join(args[1:])

    def _resolve_collection() -> str | None:
        """Pick a live vector-memory collection instead of a hardcoded one.
        The old `upwork_learning` collection no longer exists (the memory bridge
        writes sharded `agent_memory_*_v2` collections), so a hardcoded name
        returns Qdrant 404 on every query. Prefer the general shard, then any
        agent_memory_* collection actually present."""
        import requests as _rq

        try:
            resp = _rq.get(f"{_qdrant_url}/collections", timeout=5)
            if resp.status_code != 200:
                return None
            names = [
                c.get("name")
                for c in resp.json().get("result", {}).get("collections", [])
            ]
            for preferred in (
                "agent_memory_general_v2",
                "general",
                "swarm_memory",
            ):
                if preferred in names:
                    return preferred
            for n in names:
                if isinstance(n, str) and (
                    n.startswith("agent_memory_")
                    or n in ("swarm_memory", "ReflexionMemory")
                ):
                    return n
        except Exception:
            pass
        return None

    if action == "query":
        ctx.console.print(
            f"[bold cyan]Searching vector memories for: [green]{text}[/green]...[/bold cyan]"
        )
        try:
            import requests

            collection = _resolve_collection()
            if collection is None:
                ctx.console.print(
                    "[bold yellow]No vector-memory collection found on Qdrant — inject some memories first.[/bold yellow]"
                )
                return
            emb_resp = requests.post(
                _emb_url,
                json={"input": text[:7000]},
                headers={"Authorization": "Bearer llama"},
                timeout=10.0,
            )
            vector = (
                emb_resp.json().get("data", [{}])[0].get("embedding", [0.0] * 768)
                if emb_resp.status_code == 200
                else [0.0] * 768
            )
            q_resp = requests.post(
                f"{_qdrant_url}/collections/{collection}/points/search",
                json={"vector": vector, "limit": 5, "with_payload": True},
                timeout=10.0,
            )
            if q_resp.status_code == 200:
                results = q_resp.json().get("result", [])
                if not results:
                    ctx.console.print("[dim]No vector memory matches found.[/dim]")
                    return
                ctx.console.print(f"[dim]collection: {collection}[/dim]")
                table = Table(box=SIMPLE, header_style="bold cyan")
                table.add_column("Score", style="bold yellow")
                table.add_column("Memory Payload", style="white")
                for r in results:
                    table.add_row(
                        f"{r.get('score', 0.0):.2f}",
                        json.dumps(r.get("payload", {}), indent=1),
                    )
                ctx.console.print(table)
            else:
                ctx.console.print(
                    f"[bold red]Qdrant search failed with status {q_resp.status_code}.[/bold red]"
                )
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to query memory store: {e}[/bold red]")
    elif action == "inject":
        ctx.console.print(
            "[bold cyan]Injecting text into vector memory store...[/bold cyan]"
        )
        try:
            import requests, uuid

            collection = _resolve_collection()
            if collection is None:
                ctx.console.print(
                    "[bold yellow]No vector-memory collection found on Qdrant — cannot inject.[/bold yellow]"
                )
                return
            emb_resp = requests.post(
                _emb_url,
                json={"input": text[:7000]},
                headers={"Authorization": "Bearer llama"},
                timeout=10.0,
            )
            if emb_resp.status_code != 200:
                ctx.console.print("[bold red]Failed to generate embedding.[/bold red]")
                return
            vector = emb_resp.json().get("data", [{}])[0].get("embedding", [0.0] * 768)
            q_resp = requests.put(
                f"{_qdrant_url}/collections/{collection}/points",
                json={
                    "points": [
                        {
                            "id": str(uuid.uuid4()),
                            "vector": vector,
                            "payload": {
                                "text": text,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        }
                    ]
                },
                timeout=10.0,
            )
            if q_resp.status_code == 200:
                ctx.console.print(
                    f"[bold green]✓ Text stored in vector memory ({collection})![/bold green]"
                )
            else:
                ctx.console.print(
                    f"[bold red]Qdrant upsert failed with status {q_resp.status_code}.[/bold red]"
                )
        except Exception as e:
            ctx.console.print(f"[bold red]Failed to inject memory: {e}[/bold red]")


@registry.register("search", "Semantic search in Qdrant memory")
def cmd_search(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /search <query>[/yellow]")
        return
    query = " ".join(args)
    ctx.console.print(f"[cyan]Searching memory for: '{query}'...[/cyan]")
    resp = ctx.call_api("/features/search", "POST", payload={"query": query})
    if resp and resp.status_code == 200:
        data = resp.json().get("results", [])
        if not data:
            ctx.console.print("[dim]No results found.[/dim]")
            return
        table = Table(title="[bold cyan]Search Results[/bold cyan]")
        table.add_column("Score", style="green")
        table.add_column("Content", style="white")
        for item in data:
            table.add_row(
                f"{item.get('score', 0):.2f}",
                str(item.get("content", ""))[:150] + "...",
            )
        ctx.console.print(table)
    else:
        ctx.console.print("[bold red]Search failed.[/bold red]")


@registry.register(
    "heal",
    "Evaluate and trigger self-heal run. Usage: /heal run [force] | /heal stats | /heal lessons [type]",
)
def cmd_heal(ctx: CommandContext, args: List[str]) -> None:
    from organism_console.core.self_repair_engine import SelfRepairEngine
    from organism_console.core.repair_engine import load_cures, load_lessons

    if not args:
        ctx.console.print(
            "[yellow]Usage: /heal run (force) | /heal stats | /heal lessons (type)[/yellow]"
        )
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
        ctx.console.print(
            Panel(
                table,
                title="[bold magenta]Self-Heal Engine Stats[/bold magenta]",
                border_style="magenta",
            )
        )
        return
    if sub == "lessons":
        ftype = args[1] if len(args) > 1 else None
        lessons = SelfRepairEngine().show_lessons(ftype)
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
        ctx.console.print(
            Panel(
                t,
                title="[bold magenta]Repair History[/bold magenta]",
                border_style="magenta",
            )
        )
        return
    if sub != "run":
        ctx.console.print(
            "[yellow]Usage: /heal run (force) | /heal stats | /heal lessons (type)[/yellow]"
        )
        return
    ctx.console.print(
        "[bold magenta]Initiating autonomous self-heal with tiered repair...[/bold magenta]"
    )
    anomalies = 0
    resp = ctx.call_api("/healing/evaluate", "POST")
    if not resp:
        ctx.console.print(
            "[red]Heal endpoint unreachable, falling back to local diagnostics[/red]"
        )
    else:
        d = resp.json()
        color = "green" if d.get("last_heal_success", True) else "red"
        anomalies = d.get("active_anomalies", 0)
        table = Table(show_header=False, box=SIMPLE)
        table.add_row("Readiness", f"{d.get('recovery_readiness', 0)}%")
        table.add_row("Active Anomalies", str(anomalies))
        table.add_row(
            "Last Heal",
            f"[{color}]Success[/{color}]"
            if d.get("last_heal_success", True)
            else "[red]Failed[/red]",
        )
        ctx.console.print(
            Panel(
                table,
                title="[bold magenta]Healing Cycle Evaluation[/bold magenta]",
                border_style="magenta",
            )
        )
        if anomalies == 0 and "force" not in map(str.lower, args):
            ctx.console.print(
                "[green]No anomalies detected. Use `/heal run force` to run anyway.[/green]"
            )
            return
    engine = SelfRepairEngine(ctx)
    event_file = Path(__file__).parent.parent / "data" / "events" / "events.jsonl"
    if event_file.exists():
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
        if failures:
            from collections import Counter

            for err, count in Counter(failures).most_common(5):
                ctx.console.print(f"\n[bold]Error ({count}x):[/bold] {err[:100]}")
                engine.diagnose_and_repair(err)
    stats = engine.show_stats()
    ctx.console.print(
        f"\n[bold cyan]Heal Summary:[/bold cyan] T0:[green]{stats['t0_hits']}[/green] T1:[green]{stats['t1_hits']}[/green] T2:[green]{stats['t2_hits']}[/green] Failed:[red]{stats['failures']}[/red]"
    )
    ctx.console.print(
        f"[dim]Knowledge base: {sum(len(v) for v in load_cures().values())} distilled cures[/dim]"
    )
    if stats["failures"] > 0 and (anomalies > 0 or "force" in map(str.lower, args)):
        ctx.console.print(
            "\n[bold cyan]Dispatching Internet Self-Healing Goal Loop for unresolved issues...[/bold cyan]"
        )
        if ctx.run_goal_loop:
            ctx.run_goal_loop(
                "Analyze active anomalies, use web_search to research root causes, use filesystem to rewrite broken Python code."
            )
        else:
            ctx.console.print("[red]Goal loop runner unavailable.[/red]")


@registry.register(
    "upgrade",
    "Research SOTA upgrades, then apply them only after your approval. Usage: /upgrade [task]",
)
def cmd_upgrade(ctx: CommandContext, args: List[str]) -> None:
    custom_task = " ".join(args) if args else ""
    if not ctx.run_goal_loop:
        ctx.console.print("[red]Goal loop not configured.[/red]")
        return

    ctx.console.print(
        "[bold magenta]Initiating Autonomous Self-Upgrade Cycle...[/bold magenta]"
    )
    if custom_task:
        ctx.console.print(f"[cyan]Targeting: {custom_task}[/cyan]")

    # PHASE 1 — read-only research + proposal. `force_readonly=True` pins the
    # read-only branch deterministically (the proposal text must never be
    # classified as a write goal), and the objective itself uses only read-only
    # keywords so the agent researches and proposes rather than edits.
    from rich.prompt import Confirm
    from organism_console.loops.autonomous import run_autonomous_goal_loop

    research_objective = (
        "Research SOTA Python AI agent upgrades via web_search (GitHub, Arxiv, HuggingFace) "
        "and analyze this codebase. Produce a CONCRETE upgrade proposal: for each recommended "
        "change, list the file path, the exact change, the reason, and the source. Do NOT "
        "modify, write, or create any files — research and propose only."
    )
    if custom_task:
        research_objective += f"\n\nSPECIFIC USER TASK: {custom_task}"

    try:
        proposal = run_autonomous_goal_loop(
            research_objective, ctx, force_readonly=True
        )
    except Exception as e:
        ctx.console.print(f"[red]Upgrade research failed: {e}[/red]")
        return

    if not proposal or not proposal.strip():
        ctx.console.print(
            "[red]Research produced no upgrade proposal — nothing was changed.[/red]"
        )
        return

    ctx.console.print()
    ctx.console.print(
        Panel(
            Markdown(proposal),
            title="🔬 [bold green]Upgrade Proposal[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    )
    ctx.console.print()

    # GATE — the ONLY point at which the swarm may touch the working tree for
    # this cycle. Default is no; declining applies zero changes.
    if not Confirm.ask(
        "[bold yellow]Apply these upgrades? (Changes land only after you approve)[/bold yellow]",
        default=False,
    ):
        ctx.console.print("[dim]Upgrade declined — no changes were applied.[/dim]")
        return

    # PHASE 2 — apply the approved proposal through the verified write loop
    # (syntax checks → related tests → ratchet guardrail).
    apply_objective = (
        "Implement the following APPROVED upgrade proposal. For each item, read the target "
        "file first, apply the exact change described, keep existing conventions, then verify "
        "syntax and run the related tests.\n\nAPPROVED PROPOSAL:\n\n" + proposal
    )
    ctx.run_goal_loop(apply_objective)

    ctx.console.print(
        "[bold cyan]Running skill-memory self-improvement analysis...[/bold cyan]"
    )
    try:
        from organism_console.core.self_improvement_agent import SelfImprovementAgent

        agent = SelfImprovementAgent()
        upgrades = agent.analyze_and_upgrade()
        if not upgrades:
            ctx.console.print("[dim]No upgrade suggestions from skill memory.[/dim]")
        for upgrade in upgrades:
            ctx.console.print(
                f"[yellow]{upgrade['type']}:[/yellow] {upgrade['description']} [dim]({upgrade['priority']})[/dim]"
            )
            try:
                agent.execute_upgrade(upgrade)
                ctx.console.print(
                    f"[green]  ✓ Executed {upgrade['type']} upgrade[/green]"
                )
            except Exception as e:
                ctx.console.print(
                    f"[red]  ✗ Failed to execute {upgrade['type']} upgrade: {e}[/red]"
                )
    except Exception as e:
        ctx.console.print(f"[red]Self-improvement analysis failed: {e}[/red]")


@registry.register(
    "goal", "Run autonomous self-correcting loop. Usage: /goal <objective>"
)
def cmd_goal(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            "[yellow]Usage: /goal <objective>. Example: /goal fix failing tests[/yellow]"
        )
        return
    if ctx.run_goal_loop:
        ctx.run_goal_loop(" ".join(args))
    else:
        ctx.console.print("[red]Goal loop not configured.[/red]")


@registry.register(
    "debate", "Run agent planning debate before executing. Usage: /debate <goal>"
)
def cmd_debate(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            "[yellow]Usage: /debate <goal>. Example: /debate design self-healing cache[/yellow]"
        )
        return
    if ctx.run_debate:
        ctx.run_debate(" ".join(args))
    else:
        ctx.console.print("[red]Debate not configured.[/red]")


@registry.register(
    "vote",
    "Query multiple models on a prompt and show consensus. Usage: /vote <prompt>",
)
def cmd_vote(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print(
            "[yellow]Usage: /vote <prompt>. Example: /vote is python statically typed?[/yellow]"
        )
        return
    prompt = " ".join(args)
    models = list(ctx.installed_models)
    if not models:
        resp = ctx.call_api("/status")
        if resp and resp.status_code == 200:
            models = resp.json().get("installed_models", [])
    if not models:
        models = ["qwen3.5-4b", "qwen3.5-4b", "qwen3.5-4b"]
    targets = models[:3]
    while len(targets) < 3:
        targets.append(targets[0])
    ctx.console.print(
        f"[bold cyan]Submitting consensus vote queries to targets: {', '.join(targets)}...[/bold cyan]"
    )

    def run_query(model_name: str):
        try:
            resp = ctx.call_api(
                "/generate", "POST", {"model": model_name, "prompt": prompt}
            )
            if resp and resp.status_code == 200:
                return resp.json().get("response", "").strip(), model_name
            return f"Error: Status {resp.status_code if resp else 'None'}", model_name
        except Exception as e:
            return f"Error: {e}", model_name

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_query, m) for m in targets]
        results = [f.result() for f in futures]

    def compute_jaccard(text1: str, text2: str) -> float:
        w1 = set(re.findall(r"\w+", text1.lower()))
        w2 = set(re.findall(r"\w+", text2.lower()))
        if not w1 or not w2:
            return 0.0
        return len(w1.intersection(w2)) / len(w1.union(w2))

    r1, m1 = results[0]
    r2, m2 = results[1]
    r3, m3 = results[2]
    s12, s23, s13 = (
        compute_jaccard(r1, r2),
        compute_jaccard(r2, r3),
        compute_jaccard(r1, r3),
    )
    a1, a2, a3 = (s12 + s13) / 2.0, (s12 + s23) / 2.0, (s13 + s23) / 2.0
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
    ctx.console.print(
        f"[bold cyan]Overall Consensus:[/bold cyan] [bold yellow]{overall_consensus:.2%}[/bold yellow]\n"
    )
    ctx.console.print(
        Panel(
            escape(best_response),
            title=f"[bold green]Consensus Winner: {best_model} ({best_score:.2%})[/bold green]",
            border_style="green",
        )
    )


@registry.register("agents", "List all registered agents and their assigned models")
def cmd_agents(ctx: CommandContext, args: List[str]) -> None:
    resp = ctx.call_api("/agents", "GET")
    if not resp:
        ctx.console.print("[bold red]Backend offline[/bold red]")
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
            assigned_model, _ = AGENT_MODELS.get(agent_id, ("\u2014", ""))
            active = " ▶" if agent_id == ctx.state.active_agent else ""
            table.add_row(
                f"{agent_id}{active}",
                a.get("role", "?"),
                assigned_model,
                a.get("description", "")[:60],
            )
        ctx.console.print(
            Panel(
                table,
                title=f"[bold cyan]Agent Registry ({len(agents)} agents)[/bold cyan]",
                border_style="cyan",
            )
        )
    except Exception as e:
        ctx.console.print(f"[bold red]Failed to parse agents: {e}[/bold red]")


@registry.register("benchmark", "Test latency/token throughput across local models.")
def cmd_benchmark(ctx: CommandContext, args: List[str]) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    models = ctx.installed_models
    if not models:
        ctx.console.print("[yellow]No installed models found to benchmark.[/yellow]")
        return
    if args:
        target_model = args[0]
        matching_models = [m for m in models if target_model.lower() in m.lower()]
        if not matching_models:
            ctx.console.print(f"[yellow]No models matching '{target_model}'.[/yellow]")
            return
        models = matching_models
    table = Table(title="[bold cyan]LLM Benchmark[/bold cyan]", border_style="cyan")
    table.add_column("Model", style="bold green")
    table.add_column("Status", style="yellow")
    table.add_column("Latency (s)", style="magenta", justify="right")
    table.add_column("Throughput (t/s)", style="blue", justify="right")
    table.add_column("Output Preview", style="white")
    benchmark_prompt = (
        "Explain why gravity is weaker than electromagnetism in exactly 2 sentences."
    )
    ctx.console.print(f"[cyan]Benchmarking {len(models)} models...[/cyan]")

    def test_model(model_name: str):
        start = time.time()
        try:
            resp = ctx.call_api(
                "/generate",
                "POST",
                payload={"model": model_name, "prompt": benchmark_prompt},
            )
            duration = time.time() - start
            if resp and resp.status_code == 200:
                response_text = resp.json().get("response", "").strip()
                tokens = max(1, len(response_text) // 4)
                tps = tokens / max(0.1, duration)
                return (
                    model_name,
                    "[green]SUCCESS[/green]",
                    duration,
                    tps,
                    response_text[:50].replace("\n", " ") + "...",
                )
            return (
                model_name,
                "[red]FAILED[/red]",
                duration,
                0.0,
                f"HTTP {resp.status_code if resp else 'No response'}",
            )
        except Exception as e:
            return model_name, "[red]ERROR[/red]", time.time() - start, 0.0, str(e)[:50]

    with ThreadPoolExecutor(max_workers=min(len(models), 4)) as executor:
        for m, status, dur, tps, prev in executor.map(test_model, models):
            table.add_row(m, status, f"{dur:.2f}s", f"{tps:.1f} t/s", prev)
    ctx.console.print(table)


@registry.register(
    "autoassign", "Analyze models via cloud benchmarks and auto-assign to agents."
)
def cmd_autoassign(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print(
        "[cyan]Requesting dynamic AI auto-assignment from backend...[/cyan]"
    )
    resp = ctx.call_api("/models/autoassign", "POST", {})
    if not resp or resp.status_code != 200:
        ctx.console.print(
            f"[bold red]Failed to auto-assign:[/bold red] {resp.text if resp else 'No response'}"
        )
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
    ctx.console.print(
        Panel(
            table,
            title="[bold cyan]Dynamic Auto-Assignment Complete[/bold cyan]",
            border_style="cyan",
        )
    )


@registry.register(
    "clear", "Clear session history and reset context", aliases=["reset"]
)
def cmd_clear(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.history = []
    ctx.state.history_pointer = -1
    ctx.state.current_topic = "Nexus Initialization"
    ctx.state.current_summary = "Establishing connection to Zenith Swarm OS..."
    ctx.state.strategic_intent = ""
    ctx.state.delegation_chain = [ctx.state.active_agent]
    ctx.state.save()
    ctx.console.print("[green]✓ Session history cleared. Context reset.[/green]")


@registry.register("exit", "Exit the terminal session", aliases=["quit", "bye"])
def cmd_exit(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.save(sync=True)
    ctx.console.print("[bold blue]Terminal terminated.[/bold blue]")
    sys.exit(0)


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
            ctx.console.print("[bold green]Simulation triggered.[/bold green]")
        else:
            ctx.console.print("[bold red]Failed to trigger simulation.[/bold red]")
    elif action == "status":
        ctx.console.print("[cyan]Fetching simulation status...[/cyan]")
        resp = ctx.call_api("/api/admin/generation", "GET")
        if resp and resp.status_code == 200:
            data = resp.json()
            ctx.console.print(f"[green]Generation: {data.get('generation')}[/green]")
            for g in data.get("top_organisms", []):
                ctx.console.print(
                    f" - {g.get('id')} ({g.get('model')}): Fitness {g.get('fitness')}"
                )
        else:
            ctx.console.print("[bold red]Failed to fetch status.[/bold red]")
    else:
        ctx.console.print("[yellow]Unknown action. Use 'run' or 'status'.[/yellow]")


@registry.register("upwork", "Analyze an Upwork job description or URL")
def cmd_upwork(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /upwork <url_or_description>[/yellow]")
        return
    job_text = " ".join(args)
    ctx.console.print("[cyan]Running Upwork Scout analysis...[/cyan]")
    resp = ctx.call_api(
        "/features/upwork", "POST", payload={"query": job_text}, stream=True
    )
    if resp:
        from rich.live import Live

        full_response = ""
        with Live(console=ctx.console, refresh_per_second=10) as live:
            for line in resp.iter_lines():
                if line:
                    chunk = line.decode("utf-8").replace("data: ", "")
                    if chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                        full_response += data.get("content", "")
                        live.update(
                            Panel(
                                Markdown(full_response),
                                title="[bold cyan]Upwork Scout[/bold cyan]",
                                border_style="cyan",
                            )
                        )
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
    resp = ctx.call_api(
        "/features/chat-search", "POST", payload={"query": query}, stream=True
    )
    if resp:
        from rich.live import Live

        full_response = ""
        with Live(console=ctx.console, refresh_per_second=10) as live:
            for line in resp.iter_lines():
                if line:
                    chunk = line.decode("utf-8").replace("data: ", "")
                    if chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                        full_response += data.get("content", "")
                        live.update(
                            Panel(
                                Markdown(full_response),
                                title="[bold cyan]AI Librarian[/bold cyan]",
                                border_style="cyan",
                            )
                        )
                    except Exception:
                        pass
    else:
        ctx.console.print("[bold red]Chat search failed.[/bold red]")


@registry.register(
    "evolution",
    "Inspect/approve staged evolution generations. Usage: /evolution [staged|approve <gen>|status]",
)
def cmd_evolution(ctx: CommandContext, args: List[str]) -> None:
    """Evolution staging control (2026: the policy says staged_human_approved and
    now the code actually enforces it — new generations land in
    data/evolution/staged/ and become active only on explicit approval here)."""
    from swarm_os.services.evolution_daemon import (
        list_staged_generations,
        promote_staged_generation,
    )

    action = args[0].lower() if args else "staged"

    if action == "staged" or action == "list":
        staged = list_staged_generations()
        if not staged:
            ctx.console.print(
                "[yellow]No staged generations. The evolution daemon writes new "
                "generations to data/evolution/staged/ when SWARM_EVOLUTION=1.[/yellow]"
            )
            return
        table = Table(title="Staged Evolution Generations", box=SIMPLE)
        table.add_column("Gen")
        table.add_column("Population")
        table.add_column("Best Fitness")
        table.add_column("Elites")
        for g in staged:
            table.add_row(
                str(g["gen"]),
                str(g["population"]),
                f"{g['best_fitness']:.4f}",
                ", ".join(g.get("elites", [])[:3]),
            )
        ctx.console.print(table)
        ctx.console.print("[dim]Approve one with: /evolution approve <gen>[/dim]")

    elif action == "approve":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /evolution approve <gen>[/yellow]")
            return
        gen = args[1]
        res = promote_staged_generation(gen)
        if res.get("ok"):
            ctx.console.print(
                f"[green]✓ Promoted staged generation {gen} to active "
                f"(population {res.get('population')}, best_fitness {res.get('best_fitness')}).[/green]"
            )
        else:
            ctx.console.print(
                f"[bold red]✗ Promotion failed: {res.get('reason')}[/bold red]"
            )

    elif action == "status":
        from swarm_os.services.evolution_daemon import _load_population, GENOMES_PATH

        active = _load_population(GENOMES_PATH)
        staged = list_staged_generations()
        ctx.console.print(
            f"[cyan]Active population:[/cyan] {len(active)} genomes"
            f"{' (none yet)' if not active else ''}"
        )
        ctx.console.print(f"[cyan]Staged generations:[/cyan] {len(staged)}")
        if active:
            best = max((g.get("fitness", 0.0) for g in active), default=0.0)
            ctx.console.print(f"[cyan]Active best fitness:[/cyan] {best:.4f}")
        if staged:
            ctx.console.print("[dim]Run /evolution staged to inspect them.[/dim]")
    else:
        ctx.console.print(
            "[yellow]Usage: /evolution [staged|approve <gen>|status][/yellow]"
        )
