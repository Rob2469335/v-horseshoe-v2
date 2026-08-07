"""opencode-parity CLI commands: Build/Plan/Chat modes + undo/redo.

Maps the swarm's agent roster onto opencode's interaction model:
  /build   → coder (opencode-Build): full read/write/edit/run tool access
  /analyze → code_analyzer (opencode-Plan): read-only analysis, no edits
  /chat    → coordinator: conversational only
  /undo    → restore the working tree to its pre-run state
  /redo    → re-run the last prompt

The undo/redo snapshot helpers here are also used by cli.py (it snapshots the
working tree before every agentic run so `/undo` can restore exactly what the
coder/debugger changed).
"""
import ast
import difflib
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.box import SIMPLE

from organism_console.command_registry import registry
from organism_console._command_context import CommandContext

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Agents that may modify the filesystem (a run snapshot is taken for these).
EDITING_AGENTS = ("coder", "debugger", "tool-maker", "tool-runner", "executor")

# Agent -> (mode badge, rich colour)
_MODE_MAP = {
    "coder": ("BUILD", "green"),
    "debugger": ("REPAIR", "yellow"),
    "code_analyzer": ("ANALYZE", "cyan"),
    "researcher": ("RESEARCH", "blue"),
    "coordinator": ("CHAT", "magenta"),
    "reviewer": ("REVIEW", "magenta"),
}


def mode_badge(agent: Optional[str] = None) -> str:
    """Return a coloured opencode-style mode badge for an agent id."""
    label, color = _MODE_MAP.get(agent or "", (str(agent or "?").upper(), "white"))
    return f"[bold {color}]{label}[/bold {color}]"


def _git_porcelain(root: Path) -> tuple[list[str], list[str]]:
    """Return (modified_tracked, untracked) relative paths from `git status`."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=root, timeout=10,
        )
    except Exception:
        return [], []
    modified: list[str] = []
    untracked: list[str] = []
    for line in res.stdout.splitlines():
        if not line or len(line) < 3:
            continue
        code = line[:2]
        path = line[3:]
        if path.startswith('"') and path.endswith('"'):
            try:
                path = ast.literal_eval(path)
            except Exception:
                pass
        if code == "??":
            untracked.append(path)
        elif "M" in code or "A" in code or "R" in code:
            if " -> " in path:
                path = path.split(" -> ")[-1]
            modified.append(path)
    return modified, untracked


def snapshot_worktree(root: Path = PROJECT_ROOT) -> Dict:
    """Capture the current working-tree state so it can be restored by /undo.

    Returns {"tracked": {relpath: bytes}, "untracked": set(relpath),
    "untracked_content": {relpath: bytes}}. Only the files that differ from
    HEAD are captured — the agent's own pre-existing edits are what an undo
    must bring back. The untracked byte contents power the /diff-last diff
    review (baseline for files the agent edits in place).
    """
    modified, untracked = _git_porcelain(root)
    tracked = {}
    untracked_content = {}
    for rel in modified:
        path = root / rel
        if path.is_file():
            try:
                tracked[rel] = path.read_bytes()
            except OSError:
                pass
    for rel in untracked:
        path = root / rel
        if path.is_file():
            try:
                untracked_content[rel] = path.read_bytes()
            except OSError:
                pass
    return {"tracked": tracked, "untracked": set(untracked), "untracked_content": untracked_content}


def restore_snapshot(snap: Dict, root: Path = PROJECT_ROOT, scope: Optional[List[str]] = None) -> List[str]:
    """Restore a snapshot captured by snapshot_worktree(). Returns restored relpaths.

    - Tracked files that were modified are written back byte-for-byte.
    - Untracked files that did NOT exist at snapshot time (agent-created) are removed.

    With `scope` (a list of relpaths), restore ONLY those relpaths — a
    diff-scoped revert that touches exactly the files in scope and nothing else
    (2026 autonomy rollback: 'revert the evidence-justified diff', never a
    scoped-in-time whole-tree restore). Without scope, restore the full captured
    delta (the CLI /undo behavior).
    """
    restored: list[str] = []
    current_modified, current_untracked = _git_porcelain(root)
    created_by_agent = [p for p in current_untracked if p not in snap.get("untracked", set())]
    if scope is None:
        candidates = created_by_agent
    else:
        scope_set = set(scope)
        candidates = [p for p in created_by_agent if p in scope_set]
    for rel in sorted(candidates):
        path = root / rel
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
            restored.append(rel)
        except OSError:
            pass
    tracked_items = (snap.get("tracked") or {}).items()
    if scope is not None:
        scope_set = set(scope)
        tracked_items = [(rel, content) for rel, content in tracked_items if rel in scope_set]
    for rel, content in tracked_items:
        path = root / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            restored.append(rel)
        except OSError:
            pass
    return restored


def _git_show_head(root: Path, rel: str) -> Optional[bytes]:
    """Return the committed (HEAD) bytes of a file, or None if untracked/new."""
    try:
        res = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            capture_output=True, cwd=root, timeout=10,
        )
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return None


def _diff_kind(line: str) -> str:
    if line.startswith("+++") or line.startswith("---"):
        return "!"
    if line.startswith("@@"):
        return "@"
    if line.startswith("+"):
        return "+"
    if line.startswith("-"):
        return "-"
    return " "


def build_run_diff(snap: Dict, root: Path = PROJECT_ROOT) -> List[Dict]:
    """Compute the per-file unified diff of what changed since a snapshot.

    Each entry: {"path", "added", "removed", "lines": [(kind, text), ...]}
    where kind is + - space @ (hunk header) ! (file header). The snapshot's
    pre-run bytes are the baseline (falling back to HEAD for files that were
    clean pre-run), so the result is exactly what the agent changed — the
    opencode "session review" view.
    """
    modified, untracked = _git_porcelain(root)
    candidates = set(snap.get("tracked") or {})
    candidates |= set(snap.get("untracked_content") or {})
    candidates |= set(snap.get("untracked") or set())
    candidates |= set(modified) | set(untracked)

    def _read(rel: str) -> Optional[bytes]:
        p = root / rel
        try:
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None

    tracked = snap.get("tracked") or {}
    untracked_content = snap.get("untracked_content") or {}
    results: List[Dict] = []
    for rel in sorted(candidates):
        if " -> " in rel:
            continue
        old = tracked.get(rel, untracked_content.get(rel, _git_show_head(root, rel)))
        new = _read(rel)
        if old == new:
            continue
        old_text = (old or b"").decode("utf-8", errors="replace")
        new_text = (new or b"").decode("utf-8", errors="replace")
        old_lines = old_text.splitlines() if old is not None else []
        new_lines = new_text.splitlines() if new is not None else []
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2
        ))
        if not diff_lines:
            continue
        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        results.append({
            "path": rel,
            "added": added,
            "removed": removed,
            "lines": [(_diff_kind(l), l) for l in diff_lines],
        })
    return results


def render_run_diff(results: List[Dict], max_lines: int = 400) -> str:
    """Render build_run_diff() output as rich-markup lines (capped)."""
    parts: List[str] = []
    budget = max_lines
    for r in results:
        parts.append(
            f"[bold white]{escape(r['path'])}[/bold white] "
            f"[green]+{r['added']}[/green] [red]−{r['removed']}[/red]"
        )
        for kind, line in r["lines"]:
            if budget <= 0:
                parts.append("[dim]… (diff truncated)[/dim]")
                break
            budget -= 1
            txt = escape(line)
            if kind == "+":
                parts.append(f"  [green]{txt}[/green]")
            elif kind == "-":
                parts.append(f"  [red]{txt}[/red]")
            elif kind == "@":
                parts.append(f"  [cyan]{txt}[/cyan]")
            elif kind == "!":
                parts.append(f"  [bright_black]{txt}[/bright_black]")
            else:
                parts.append(f"  [white]{txt}[/white]")
    return "\n".join(parts)


def last_snapshot(ctx: CommandContext) -> Optional[Dict]:
    stack = getattr(ctx.state, "undo_stack", None)
    return stack[-1] if stack else None


@registry.register("permissions", "Show or edit the permission policy table (allow/ask/deny)")
def cmd_permissions(ctx: CommandContext, args: List[str]) -> None:
    from organism_console import permissions as perms
    if not args:
        rows = sorted(perms.all_policies().items())
        table = Table(box=SIMPLE, header_style="bold cyan", title="Permission policy")
        table.add_column("Tool", style="bold green")
        table.add_column("Policy", style="bold yellow")
        for tool, policy in rows:
            color = {"allow": "green", "ask": "yellow", "deny": "red"}.get(policy, "white")
            table.add_row(tool, f"[{color}]{policy}[/{color}]")
        ctx.console.print(table)
        auto = perms.auto_mode()
        ctx.console.print(f"[dim]auto-approve: [bold]{'on' if auto else 'off'}[/bold] (toggle with /auto)[/dim]")
        ctx.console.print("[dim]usage: /permissions <tool> allow|ask|deny — tools: read write patch grep glob web_search web_fetch sandbox_repl system screen healing approval git[/dim]")
        return
    if len(args) != 2:
        ctx.console.print("[yellow]Usage: /permissions <tool> allow|ask|deny[/yellow]")
        return
    tool, policy = args[0].lower(), args[1].lower()
    if perms.set_policy(tool, policy):
        ctx.console.print(f"[green]✓[/green] [white]{tool}[/white] → [bold]{policy}[/bold]")
    else:
        ctx.console.print(f"[red]✗[/red] unknown tool or policy: [white]{tool} {policy}[/white]")


@registry.register("auto", "Toggle auto-approve mode (approves anything not explicitly denied)")
def cmd_auto(ctx: CommandContext, args: List[str]) -> None:
    from organism_console import permissions as perms
    want = args[0].lower() if args else None
    if want in ("on", "1", "true", "yes"):
        on = True
    elif want in ("off", "0", "false", "no"):
        on = False
    else:
        on = not perms.auto_mode()
    perms.set_auto_mode(on)
    ctx.state.save()
    state = "ON" if on else "off"
    ctx.console.print(
        f"[yellow]auto-approve {state}[/yellow] — "
        f"[dim]{'non-denied actions run without prompting' if on else 'actions prompt for approval again'}.[/dim]"
    )


@registry.register("toasts", "Toggle desktop attention notifications on run completion / questions")
def cmd_toasts(ctx: CommandContext, args: List[str]) -> None:
    want = args[0].lower() if args else None
    if want in ("on", "1", "true", "yes"):
        on = True
    elif want in ("off", "0", "false", "no"):
        on = False
    else:
        on = not getattr(ctx.state, "toasts_enabled", True)
    ctx.state.toasts_enabled = on
    ctx.state.save()
    from organism_console.notifications import set_enabled
    set_enabled(on)
    ctx.console.print(f"[dim]desktop notifications [bold]{'on' if on else 'off'}[/bold][/dim]")


@registry.register("diff-last", "Show a unified diff of what the last agent run changed", aliases=["changes"])
def cmd_diff_last(ctx: CommandContext, args: List[str]) -> None:
    snap = last_snapshot(ctx)
    if not snap:
        ctx.console.print("[yellow]No previous run snapshot — nothing to diff.[/yellow]")
        return
    results = build_run_diff(snap)
    if not results:
        ctx.console.print("[dim]Last run made no file changes.[/dim]")
        return
    ctx.console.print(Panel(
        render_run_diff(results),
        title=f"[bold cyan]Changes from last run ({len(results)} file{'s' if len(results) != 1 else ''})[/bold cyan]",
        border_style="cyan",
    ))


@registry.register("build", "BUILD mode: plain prompts edit files via the coder agent (opencode-Build)")
def cmd_build(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.active_agent = "coder"
    ctx.state.delegation_chain = ["coder"]
    ctx.state.save()
    ctx.console.print(
        f"{mode_badge('coder')} [green]✓[/green] BUILD mode — "
        "[white]I read, write, edit, run code, and verify my own changes, "
        "like opencode's Build agent.[/white]"
    )


@registry.register("analyze", "ANALYZE mode: read-only analysis via code_analyzer (opencode-Plan)")
def cmd_analyze(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.active_agent = "code_analyzer"
    ctx.state.delegation_chain = ["code_analyzer"]
    ctx.state.save()
    ctx.console.print(
        f"{mode_badge('code_analyzer')} [cyan]✓[/cyan] ANALYZE mode — "
        "[white]I audit/explain only and do not modify files (opencode-Plan).[/white]"
    )


@registry.register("chat", "CHAT mode: conversational replies via the coordinator")
def cmd_chat(ctx: CommandContext, args: List[str]) -> None:
    ctx.state.active_agent = "coordinator"
    ctx.state.delegation_chain = ["coordinator"]
    ctx.state.save()
    ctx.console.print(
        f"{mode_badge('coordinator')} [magenta]✓[/magenta] CHAT mode — "
        "[white]conversation only; use /build when you want changes made.[/white]"
    )


@registry.register("undo", "Revert file changes made by the last agent run")
def cmd_undo(ctx: CommandContext, args: List[str]) -> None:
    stack = getattr(ctx.state, "undo_stack", None)
    if not stack:
        ctx.console.print("[yellow]Nothing to undo — no agent run has been snapshotted yet.[/yellow]")
        return
    snap = stack.pop()
    restored = restore_snapshot(snap)
    ctx.state.save()
    if not restored:
        ctx.console.print("[dim]Undo: no file changes were detected for the last run.[/dim]")
        return
    ctx.console.print(
        Panel(
            "\n".join(f"  [red]↺[/red] {escape(r)}" for r in restored[:40])
            + (f"\n  [dim]… {len(restored) - 40} more[/dim]" if len(restored) > 40 else ""),
            title=f"[bold yellow]Undo — reverted {len(restored)} file(s)[/bold yellow]",
            border_style="yellow",
        )
    )


@registry.register("redo", "Re-run the last prompt (e.g. after /undo)")
def cmd_redo(ctx: CommandContext, args: List[str]) -> Optional[str]:
    last_prompt = getattr(ctx.state, "last_prompt", "")
    if not last_prompt:
        ctx.console.print("[yellow]No last prompt to re-run.[/yellow]")
        return None
    ctx.console.print(f"[bold cyan]↻[/bold cyan] Re-running last prompt: [dim]{last_prompt[:80]}[/dim]")
    return last_prompt


@registry.register("modes", "Show the current agent mode and how to switch (BUILD/ANALYZE/CHAT)")
def cmd_modes(ctx: CommandContext, args: List[str]) -> None:
    table_rows = [
        f"{mode_badge('coder')}       [white]/build[/white] — edit files, run code, verify",
        f"{mode_badge('code_analyzer')}   [white]/analyze[/white] — read-only analysis",
        f"{mode_badge('coordinator')}   [white]/chat[/white] — conversation only",
    ]
    ctx.console.print(
        Panel(
            "\n".join(table_rows),
            title=f"Current mode: {mode_badge(ctx.state.active_agent)}",
            border_style="blue",
        )
    )
