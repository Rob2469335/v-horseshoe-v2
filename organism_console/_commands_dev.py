"""Development CLI commands: git, editing, debugging, planning."""
import subprocess
import sys
import re
from pathlib import Path
from typing import List, Optional

from rich.panel import Panel
from rich.table import Table
from rich.box import SIMPLE
from rich.markup import escape
from rich.tree import Tree

from organism_console.command_registry import registry
from organism_console._command_context import CommandContext
from organism_console._command_deps import get_forward_dependencies, get_reverse_dependencies


@registry.register("diff", "Show Git diff of current changes in workspace")
def cmd_diff(ctx: CommandContext, args: List[str]) -> None:
    try:
        result = subprocess.run(
            ["git", "diff"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=Path(__file__).parent.parent.resolve()
        )
        diff_text = result.stdout or ""
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


@registry.register("commit", "Create a Conventional Commit with an AI-generated message. Usage: /commit")
def cmd_commit(ctx: CommandContext, args: List[str]) -> None:
    project_root = Path(__file__).parent.parent.resolve()
    try:
        diff_res = subprocess.run(["git", "diff"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
        diff_staged = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
        diff_text = (diff_res.stdout or "") + "\n" + (diff_staged.stdout or "")
        if not diff_text.strip():
            ctx.console.print("[yellow]No changes detected in repository workspace.[/yellow]")
            return
        ctx.console.print("[bold cyan]Analyzing diff and generating Conventional Commit message...[/bold cyan]")
        prompt = f"Analyze this git diff and write a concise, professional commit message adhering strictly to Conventional Commits:\n\n{diff_text[:3000]}\n\nYour output must follow this format:\n<type>(<scope>): <short description>\n\nDo not output any introductory or concluding text, only the commit message itself."
        model = ctx.state.active_model or "qwen3.5-4b"
        resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
        if resp and resp.status_code == 200:
            commit_msg = resp.json().get("response", "").strip().splitlines()[0]
            ctx.console.print(Panel(commit_msg, title="Generated Commit Message", border_style="green"))
            from rich.prompt import Confirm
            if sys.stdin.isatty() and Confirm.ask("[bold yellow]Do you want to stage all changes and commit?[/bold yellow]"):
                subprocess.run(["git", "add", "."], cwd=project_root)
                commit_res = subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
                ctx.console.print("[green]✓ git add . executed.[/green]")
                if commit_res.returncode == 0:
                    ctx.console.print("[bold green]✓ Successfully committed changes![/bold green]")
                else:
                    ctx.console.print(f"[bold red]Failed to commit: {commit_res.stderr or ''}[/bold red]")
        else:
            ctx.console.print("[bold red]Failed to generate commit message from backend.[/bold red]")
    except Exception as e:
        ctx.console.print(f"[bold red]Commit command error: {e}[/bold red]")


@registry.register("branch", "Create or checkout a Git branch. Usage: /branch <branch_name>")
def cmd_branch(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        project_root = Path(__file__).parent.parent.resolve()
        res = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
        if res.returncode == 0:
            ctx.console.print(f"Active branch: [bold green]{(res.stdout or '').strip()}[/bold green]")
        else:
            ctx.console.print("[red]Failed to get current branch.[/red]")
        return
    branch_name = args[0].strip()
    project_root = Path(__file__).parent.parent.resolve()
    ctx.console.print(f"[bold cyan]Checking out branch [green]{branch_name}[/green]...[/bold cyan]")
    checkout_res = subprocess.run(["git", "checkout", branch_name], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
    if checkout_res.returncode != 0:
        checkout_res = subprocess.run(["git", "checkout", "-b", branch_name], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root)
    if checkout_res.returncode == 0:
        ctx.console.print(f"[bold green]✓ Switched to branch '{branch_name}'[/bold green]")
    else:
        ctx.console.print(f"[bold red]Failed to switch branch: {checkout_res.stderr or ''}[/bold red]")


@registry.register("debug", "Run a script/command and analyze failures. Usage: /debug <command>")
def cmd_debug(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /debug <command>. Example: /debug python main.py[/yellow]")
        return
    command = args
    ctx.console.print(f"[bold cyan]Executing command: [white]{' '.join(command)}[/white]...[/bold cyan]")
    project_root = Path(__file__).parent.parent.resolve()
    try:
        cmd_target = " ".join(command) if sys.platform == "win32" else command
        res = subprocess.run(cmd_target, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=project_root, timeout=60, shell=(sys.platform == "win32"))
        stdout = res.stdout or ""
        stderr = res.stderr or ""
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
    from organism_console.command_registry import run_syntax_checks
    syntax_passed, syntax_error_msg = run_syntax_checks(project_root)
    if not syntax_passed:
        ctx.console.print(Panel(syntax_error_msg, title="[bold red]SYNTAX ERROR DETECTED IN MODIFIED FILES[/bold red]", border_style="bold red"))
    ctx.console.print("[bold cyan]Submitting failure trace to LLM for automated diagnostic guide...[/bold cyan]")
    prompt = f"The following developer command failed:\nCommand: {' '.join(command)}\nExit Code: {exit_code}\n\nStderr / Traceback:\n{stderr or stdout}\n\nExplain what caused this crash and provide a clear, step-by-step diagnostic guide on how to fix it."
    model = ctx.state.active_model or "qwen3.5-4b"
    resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
    if resp and resp.status_code == 200:
        diag = resp.json().get("response", "").strip()
        ctx.console.print(Panel(diag, title="AI Diagnostic Guide", border_style="yellow"))
        from rich.prompt import Confirm
        if sys.stdin.isatty() and Confirm.ask("[bold yellow]Would you like the agent to automatically repair this crash?[/bold yellow]"):
            goal_text = f"Fix the crash/failure of command '{' '.join(command)}' which failed with traceback:\n{stderr or stdout}"
            if ctx.run_goal_loop:
                ctx.run_goal_loop(goal_text)
            else:
                ctx.console.print("[red]Goal loop runner unavailable.[/red]")
    else:
        ctx.console.print("[bold red]Failed to fetch diagnostic guide from backend.[/bold red]")


@registry.register("plan", "Show plan details or draft templates. Usage: /plan [create <objective>]")
def cmd_plan(ctx: CommandContext, args: List[str]) -> None:
    if args and args[0].lower() == "create":
        if len(args) < 2:
            ctx.console.print("[yellow]Usage: /plan create <objective>[/yellow]")
            return
        objective = " ".join(args[1:])
        ctx.console.print(f"[bold cyan]Generating structured developer templates for: [green]{objective}[/green]...[/bold cyan]")
        prompt = f"""You are an elite software architect. Create a structured markdown Implementation Plan for: "{objective}".
Structure: Goal Description, Proposed Changes (files to modify), Verification Plan (tests). Return ONLY markdown."""
        model = ctx.state.active_model or "qwen3.5-4b"
        try:
            resp = ctx.call_api("/generate", "POST", {"model": model, "prompt": prompt})
            if resp and resp.status_code == 200:
                plan_text = resp.json().get("response", "").strip()
                docs_dir = Path(__file__).parent.parent / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                (docs_dir / "implementation_plan.md").write_text(plan_text, encoding="utf-8")
                task_lines = [f"# Task Checklist: {objective}", "- [ ] Phase 1: Preparation", "- [ ] Phase 2: Implementation", "- [ ] Phase 3: Testing & Verification"]
                (docs_dir / "task.md").write_text("\n".join(task_lines), encoding="utf-8")
                walkthrough_lines = [f"# Walkthrough: {objective}", "## Summary of Changes Made", "## Verification & Test Results"]
                (docs_dir / "walkthrough.md").write_text("\n".join(walkthrough_lines), encoding="utf-8")
                ctx.console.print(Panel(plan_text, title="Generated Implementation Plan", border_style="green"))
                ctx.console.print("[bold green]✓ Templates created under docs/[/bold green]")
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
    from rich.prompt import Prompt, Confirm
    ctx.console.print(f"[bold cyan]Patching file: [green]{file_path.name}[/green]...[/bold cyan]")
    target_content = Prompt.ask("Enter the EXACT code block to replace (use '\\n' for newlines)").replace("\\n", "\n")
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
        ctx.console.print("[bold red]Error: Target content not found. Indentation must match exactly.[/bold red]")
        return
    if occurrences > 1:
        ctx.console.print(f"[bold red]Error: Target content is ambiguous ({occurrences} occurrences). Provide a unique block.[/bold red]")
        return
    replacement_content = Prompt.ask("Enter the NEW replacement code block (use '\\n' for newlines)").replace("\\n", "\n")
    if ctx.state.mode == "safe" and not Confirm.ask("[bold yellow]Are you sure you want to apply this patch?[/bold yellow]"):
        ctx.console.print("[yellow]Cancelled: Patch not applied.[/yellow]")
        return
    try:
        file_path.write_text(content.replace(target_content, replacement_content), encoding="utf-8")
        ctx.console.print(f"[bold green]✓ Patch successfully applied to {file_path.name}![/bold green]")
    except Exception as e:
        ctx.console.print(f"[bold red]Error writing file: {e}[/bold red]")


@registry.register("impact", "Show static AST import dependency impact of a file. Usage: /impact <file_path>")
def cmd_impact(ctx: CommandContext, args: List[str]) -> None:
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


@registry.register("map", "Map the codebase and save to .swarm_brain/repo_map.md")
def cmd_map(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print("[blue]Mapping codebase...[/blue]")
    try:
        import os as _os
        if _os.getcwd() not in sys.path:
            sys.path.insert(0, _os.getcwd())
        from runtime_v2.services.mapper import generate_repo_map
        map_content = generate_repo_map(_os.getcwd())
        brain_dir = Path(".swarm_brain")
        brain_dir.mkdir(exist_ok=True)
        (brain_dir / "repo_map.md").write_text(map_content, encoding="utf-8")
        approx_tokens = len(map_content) // 4
        ctx.console.print(f"[green]✔ Codebase mapped! (~{approx_tokens:,} tokens) Saved to .swarm_brain/repo_map.md[/green]")
    except Exception as e:
        ctx.console.print(f"[red]Error mapping codebase: {e}[/red]")


@registry.register("index", "Index the codebase into Qdrant vector database.")
def cmd_index(ctx: CommandContext, args: List[str]) -> None:
    ctx.console.print("[blue]Building semantic codebase index...[/blue]")
    try:
        import os as _os
        if _os.getcwd() not in sys.path:
            sys.path.insert(0, _os.getcwd())
        from runtime_v2.services.indexer import index_codebase
        files, chunks = index_codebase(_os.getcwd())
        ctx.console.print(f"[green]✔ Codebase indexed! ({files} files, {chunks} chunks)[/green]")
    except Exception as e:
        ctx.console.print(f"[red]Error indexing codebase: {e}[/red]")


@registry.register("compress", "Summarize older turns in history to free up context window.")
def cmd_compress(ctx: CommandContext, args: List[str]) -> None:
    history = ctx.state.history
    if not history or len(history) <= 4:
        ctx.console.print("[yellow]History is too short to compress (requires > 4 messages).[/yellow]")
        return
    to_summarize = history[:-4]
    keep = history[-4:]
    conv_text = ""
    for msg in to_summarize:
        conv_text += f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}\n\n"
    prompt = f"Summarize the following conversation in 2-3 sentences focusing on key actions and decisions:\n\n{conv_text}"
    fast_model = next((m for m in ctx.installed_models if "3b" in m or "7b" in m), "qwen3.5-4b")
    ctx.console.print(f"[cyan]Compressing {len(to_summarize)} messages using [bold green]{fast_model}[/bold green]...[/cyan]")
    try:
        resp = ctx.call_api("/generate", "POST", {"model": fast_model, "prompt": prompt})
        if resp and resp.status_code == 200:
            summary = resp.json().get("response", "").strip()
            ctx.state.history = [{"role": "system", "content": f"[Conversation Compressed: {summary}]"}] + keep
            ctx.state.save()
            ctx.console.print("[green]✓ History compressed![/green]")
            ctx.console.print(Panel(summary, title="[bold cyan]Compressed Summary[/bold cyan]", border_style="cyan"))
        else:
            ctx.console.print("[red]Failed to generate summary.[/red]")
    except Exception as e:
        ctx.console.print(f"[red]Error during compression: {e}[/red]")


@registry.register("schedule", "Run a command on a recurring schedule. Usage: /schedule <interval> <prompt> | /schedule list | /schedule clear")
def cmd_schedule(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /schedule <seconds> <command> | /schedule list | /schedule clear[/yellow]")
        return
    subcmd = args[0].lower()
    if subcmd == "list":
        if not getattr(ctx.state, "scheduled_tasks", None):
            ctx.console.print("[dim]No scheduled tasks configured.[/dim]")
            return
        table = Table(title="Scheduled Tasks", border_style="cyan")
        table.add_column("ID", style="bold")
        table.add_column("Interval", style="yellow")
        table.add_column("Command / Prompt", style="green")
        for idx, task in enumerate(ctx.state.scheduled_tasks, 1):
            table.add_row(str(idx), str(task.get("interval", "")), str(task.get("command", "")))
        ctx.console.print(table)
        return
    if subcmd == "clear":
        ctx.state.scheduled_tasks = []
        ctx.state.save()
        ctx.console.print("[green]✓ All scheduled tasks cleared.[/green]")
        return
    if len(args) < 2:
        ctx.console.print("[yellow]Usage: /schedule <seconds> <command>[/yellow]")
        return
    if not getattr(ctx.state, "scheduled_tasks", None):
        ctx.state.scheduled_tasks = []
    ctx.state.scheduled_tasks.append({"interval": args[0], "command": " ".join(args[1:]), "created_at": __import__('time').time()})
    ctx.state.save()
    ctx.console.print("[bold green]✓ Scheduled task registered[/bold green]")


@registry.register("checkpoint", "Save a named time-travel snapshot. Usage: /checkpoint <name>")
def cmd_checkpoint(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /checkpoint <name>[/yellow]")
        return
    name = args[0]
    if hasattr(ctx.state, "create_checkpoint") and ctx.state.create_checkpoint(name):
        ctx.console.print(f"[bold green]✓ Session checkpoint saved: [cyan]{escape(name)}[/cyan][/bold green]")
    else:
        ctx.console.print(f"[red]Error saving checkpoint '{escape(name)}'.[/red]")


@registry.register("rollback", "Restore a named session checkpoint. Usage: /rollback <name>")
def cmd_rollback(ctx: CommandContext, args: List[str]) -> None:
    if not args:
        ctx.console.print("[yellow]Usage: /rollback <name>[/yellow]")
        return
    name = args[0]
    if hasattr(ctx.state, "rollback_checkpoint") and ctx.state.rollback_checkpoint(name):
        ctx.console.print(f"[bold green]✓ Rolled back to checkpoint: [cyan]{escape(name)}[/cyan][/bold green]")
    else:
        ctx.console.print(f"[red]Checkpoint '{escape(name)}' not found.[/red]")


@registry.register("checkpoints", "List all saved session checkpoints.")
def cmd_list_checkpoints(ctx: CommandContext, args: List[str]) -> None:
    cps = getattr(ctx.state, "checkpoints", {})
    if not cps:
        ctx.console.print("[yellow]No checkpoints saved. Use `/checkpoint <name>` to create one.[/yellow]")
        return
    ctx.console.print("[bold cyan]=== Saved Session Checkpoints ===[/bold cyan]")
    for name, cp in cps.items():
        turns = len(cp.get("history", []))
        ctx.console.print(f"  • [green]{escape(name)}[/green]: {turns} turns stored")


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
        f"[bold yellow]Role[/bold yellow]: {run.get('role', 'unknown')}\n"
        f"[bold yellow]Content[/bold yellow]: {escape(run.get('content', '')[:500])}...",
        title=f"[bold cyan]History Explorer (Run #{ctx.state.history_pointer})[/bold cyan]",
        border_style="cyan"
    ))


@registry.register("next", "Step forward in execution history")
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
