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
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from rich.markup import escape
from rich.panel import Panel

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

    Returns {"tracked": {relpath: bytes}, "untracked": set(relpath)}. Only the
    files that differ from HEAD are captured — the agent's own pre-existing
    edits are what an undo must bring back.
    """
    modified, untracked = _git_porcelain(root)
    tracked = {}
    for rel in modified:
        path = root / rel
        if path.is_file():
            try:
                tracked[rel] = path.read_bytes()
            except OSError:
                pass
    return {"tracked": tracked, "untracked": set(untracked)}


def restore_snapshot(snap: Dict, root: Path = PROJECT_ROOT) -> List[str]:
    """Restore a snapshot captured by snapshot_worktree(). Returns restored relpaths.

    - Tracked files that were modified are written back byte-for-byte.
    - Untracked files that did NOT exist at snapshot time (agent-created) are removed.
    """
    restored: list[str] = []
    current_modified, current_untracked = _git_porcelain(root)
    created_by_agent = [p for p in current_untracked if p not in snap.get("untracked", set())]
    for rel in sorted(created_by_agent):
        path = root / rel
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
            restored.append(rel)
        except OSError:
            pass
    for rel, content in (snap.get("tracked") or {}).items():
        path = root / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            restored.append(rel)
        except OSError:
            pass
    return restored


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
