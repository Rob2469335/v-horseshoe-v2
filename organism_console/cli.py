"""ZENITH CLI — 2027 Edition

Usage:
  python -m organism_console          # Interactive REPL
  python -m organism_console <cmd>    # Run one command
  python -m organism_console --version
  python -m organism_console --agent coder
  python -m organism_console --model ibm/granite4.1:8b
"""

from __future__ import annotations

import sys
import json
import logging
import os
import atexit
import signal
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.panel import Panel

from organism_console.config import SESSION_FILE, LOG_DIR, VERSION
from organism_console.state_store import SessionState
from organism_console.command_registry import registry, CommandContext

from organism_console.api_client import call_api
from organism_console.ui.banner import print_banner, get_system_stats, estimate_tokens
from organism_console.ui.live_stream import stream_prompt_with_retry
from organism_console.loops.autonomous import run_autonomous_goal_loop
from organism_console.loops.debate import run_debate_loop


LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(rich_tracebacks=True),
        logging.FileHandler(LOG_DIR / "cli.log"),
    ],
)
log = logging.getLogger("zenith_cli")


class CLIContext(SessionState):
    def __init__(self):
        super().__init__(SESSION_FILE)
        self.console = Console(highlight=False)

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


_installed_models_cache = None


def get_installed_models():
    # BUG FIX: Use a global cache that ONLY caches successful backend API responses,
    # preventing permanent fallback caching when the backend is temporarily offline.
    global _installed_models_cache
    if _installed_models_cache is not None:
        return _installed_models_cache

    try:
        resp = call_api("/status")
        if resp:
            _installed_models_cache = tuple(resp.json().get("installed_models", []))
            return _installed_models_cache
    except Exception:
        pass

    try:
        from runtime_v2.services.model_registry import get_model

        coordinator_model, _ = get_model("coordinator")
        return (coordinator_model,)
    except Exception:
        return ("qwen3.5-4b",)


def build_command_context(cmd_ctx_console=None, cmd_ctx_state=None) -> CommandContext:
    """Build a CommandContext with proper closure bindings."""
    _state = cmd_ctx_state or ctx
    _console = cmd_ctx_console or ctx.console
    _installed = get_installed_models()

    _ctx = CommandContext(
        state=_state,
        console=_console,
        call_api=call_api,
        run_prompt=lambda p: stream_prompt_with_retry(
            ctx, ctx.active_agent, p, ctx.history
        ),
        get_system_stats=get_system_stats,
        installed_models=_installed,
    )
    _ctx.run_goal_loop = lambda g, __ctx=_ctx: run_autonomous_goal_loop(g, __ctx)
    _ctx.run_debate = lambda g, __ctx=_ctx: run_debate_loop(g, __ctx)
    _ctx.run_prompt_with_agent = lambda agent, prompt, __state=ctx: (
        stream_prompt_with_retry(__state, agent, prompt, __state.history)
    )
    return _ctx


def run_agentic(
    ctx: SessionState, execute_prompt: str, json_flag: bool = False
) -> dict:
    """Run a prompt through the active agent (opencode-parity BUILD behavior).

    Snapshots the working tree before an editing agent runs so `/undo` can
    restore exactly what it changed, remembers the prompt for `/redo`, and
    prints a diff review of what the run actually changed. `ctx` is the
    session state itself (CLIContext / SessionState) — `last_prompt` and
    `undo_stack` live directly on it.
    """
    import time as _time
    from organism_console._commands_opencode import (
        EDITING_AGENTS,
        build_run_diff,
        snapshot_worktree,
    )

    ctx.last_prompt = execute_prompt
    snap = None
    if ctx.active_agent in EDITING_AGENTS:
        try:
            snap = snapshot_worktree()
            ctx.undo_stack.append(snap)
            ctx.undo_stack = ctx.undo_stack[-5:]
        except Exception:
            snap = None
    _t0 = _time.time()
    result_history = stream_prompt_with_retry(
        ctx, ctx.active_agent, execute_prompt, ctx.history
    )
    ctx.history = result_history
    elapsed = _time.time() - _t0
    content = ""
    if (
        result_history
        and isinstance(result_history[-1], dict)
        and result_history[-1].get("role") == "assistant"
    ):
        content = str(result_history[-1].get("content", ""))
    files_changed = []
    if snap:
        try:
            files_changed = build_run_diff(snap)
        except Exception:
            files_changed = []
        if files_changed:
            summary = "\n".join(
                f"  [bold white]{escape(r['path'])}[/bold white] "
                f"[green]+{r['added']}[/green] [red]−{r['removed']}[/red]"
                for r in files_changed
            )
            ctx.console.print(
                Panel(
                    summary,
                    title=f"[bold green]Changed files ({len(files_changed)})[/bold green] — [dim]/diff-last to view[/dim]",
                    border_style="green",
                )
            )
    if getattr(ctx, "toasts_enabled", True) and elapsed > 10 and not json_flag:
        from organism_console.notifications import notify

        notify(
            "ZENITH run complete",
            f"{ctx.active_agent} finished in {elapsed:.0f}s"
            + (f" · {len(files_changed)} file(s) changed" if files_changed else ""),
        )
    return {
        "history": result_history,
        "content": content,
        "files_changed": files_changed,
        "elapsed": elapsed,
    }


def print_version():
    ctx.console.print(f"[bold cyan]ZENITH CLI[/bold cyan] [dim]v{VERSION}[/dim]")
    ctx.console.print(f"[dim]Python {sys.version.split()[0]} on {sys.platform}[/dim]")


def print_help():
    ctx.console.print(f"[bold cyan]ZENITH CLI[/bold cyan] [dim]v{VERSION}[/dim]")
    ctx.console.print(
        "[bold]Usage:[/bold] python -m organism_console [OPTIONS] [COMMAND / SLASH_COMMAND]"
    )
    ctx.console.print("\n[bold cyan]Command-Line Flags:[/bold cyan]")
    ctx.console.print("  [green]--agent <name>[/green]   Override initial active agent")
    ctx.console.print("  [green]--model <name>[/green]   Override initial active model")
    ctx.console.print("  [green]--version, -v[/green]    Show version information")
    ctx.console.print(
        "  [green]--help, -h[/green]       Show help message and available slash commands\n"
    )
    ctx.console.print("[bold cyan]Available Slash Commands:[/bold cyan]")
    cmd_ctx = build_command_context()
    registry.handle_line("/help", cmd_ctx)


def handle_sigint(sig, _frame):
    # BUG FIX: Raise KeyboardInterrupt so it propagates to the REPL's except block.
    # Previously this only printed a newline, meaning Ctrl+C during a long LLM stream
    # would print a blank line but NOT interrupt the blocking stream_prompt() call.
    ctx.console.print()
    raise KeyboardInterrupt


def setup_readline():
    """Set up command history with readline if available."""
    try:
        import readline

        histfile = LOG_DIR / ".cli_history"
        try:
            readline.read_history_file(str(histfile))
        except FileNotFoundError:
            pass
        readline.set_history_length(500)
        atexit.register(readline.write_history_file, str(histfile))

        def completer(text, state):
            cmd_list = [
                f"/{k}"
                for k in registry.commands.keys()
                if k.startswith(text.lower()) or f"/{k}".startswith(text)
            ]
            try:
                return cmd_list[state]
            except IndexError:
                return None

        readline.set_completer(completer)
        if sys.platform != "win32":
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind("set bell-style none")
        return True
    except ImportError:
        return False


def main():
    ctx.cloud_enabled = False
    # Default to local-first routing with cloud fan-out (DeepSeek + ultra-cheap
    # Ling) as fallback. `/local` forces the fully offline local_only mode.
    os.environ.setdefault("SWARM_ROUTING_MODE", "auto")

    from organism_console.token_tracker import start_background_poll

    start_background_poll()

    # --- CLI arg parsing ---
    args = sys.argv[1:]
    if "--version" in args or "-v" in args:
        print_version()
        return 0
    if "--help" in args or "-h" in args:
        print_help()
        return 0

    # Parse --agent and --model flags (plus --continue / --json passthrough)
    continue_flag = "--continue" in args
    json_flag = "--json" in args
    agent_override = None
    model_override = None
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--agent" and i + 1 < len(args):
            agent_override = args[i + 1]
            i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            model_override = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1
    args = filtered_args

    if agent_override:
        ctx.active_agent = agent_override
        ctx.delegation_chain = [agent_override]
    else:
        # opencode-parity default: BUILD mode (coder) — plain prompts edit files.
        ctx.active_agent = "coder"
        ctx.delegation_chain = ["coder"]
    if model_override:
        ctx.active_model = model_override

    signal.signal(signal.SIGINT, handle_sigint)

    # --- Single command mode ---
    if args:
        cmd_line = " ".join(args)
        cmd_ctx = build_command_context()
        execute_prompt = registry.handle_line(cmd_line, cmd_ctx)
        try:
            result = (
                run_agentic(ctx, execute_prompt, json_flag) if execute_prompt else {}
            )
            if json_flag:
                print(
                    json.dumps(
                        {
                            "ok": bool(result.get("content")),
                            "agent": ctx.active_agent,
                            "model": ctx.active_model,
                            "prompt": execute_prompt or cmd_line,
                            "content": result.get("content", ""),
                            "files_changed": [
                                r["path"] for r in result.get("files_changed", [])
                            ],
                        },
                        indent=2,
                    )
                )
        except KeyboardInterrupt:
            # BUG FIX: graceful Ctrl+C in single-command mode — match the REPL
            # (which catches KeyboardInterrupt) instead of dumping a raw traceback
            # when the user aborts a long single-shot run mid-stream.
            ctx.console.print("\n[dim]Interrupted.[/dim]")
            if json_flag:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "agent": ctx.active_agent,
                            "content": "",
                            "files_changed": [],
                        }
                    )
                )
        return 0
    # --- Interactive REPL ---
    setup_readline()
    from organism_console.notifications import set_enabled as _set_toasts

    _set_toasts(getattr(ctx, "toasts_enabled", True))
    if continue_flag:
        ctx.console.print(
            f"[bold cyan]↻ Resumed session[/bold cyan] [dim]— {len(ctx.history)} history turn(s), agent: {ctx.active_agent}[/dim]"
        )
    else:
        print_banner(ctx)

    # Auto-start the background healing watchmen so both infrastructure health
    # AND code-level repair are self-healed even when no /goal loop is running.
    from organism_console.core.healing_watchman import HealingWatchman

    _healing_watchman = HealingWatchman(interval_seconds=60.0, console=ctx.console)
    _healing_watchman.start()
    atexit.register(_healing_watchman.stop)

    try:
        from organism_console.core.self_repair_engine import SelfRepairEngine
        from organism_console.core.repair_engine import RepairWatchman

        # Coexistence guard (2026): when the backend's autonomous watch-loop is
        # enabled (SWARM_AUTONOMY=1, the default), the CLI must NOT start its own
        # code-repair watchman. Two independent tailers on the same events.jsonl
        # would double-dispatch repairs on the same file and race on the shared
        # repair_breaker.json / repair_lessons.jsonl state (no cross-process lock).
        # One code-repair tailer, one engine, one writer to the shared state.
        import os as _os_cli

        if _os_cli.environ.get("SWARM_AUTONOMY", "1").strip() != "1":
            _repair_watchman = RepairWatchman(
                SelfRepairEngine(build_command_context()), interval_seconds=30
            )
            _repair_watchman.start(start_at_end=True)
            atexit.register(_repair_watchman.stop)
            ctx.console.print(
                "[dim]⚕ Auto-repair watchman active (tailing events.jsonl for code failures)[/dim]"
            )
        else:
            log.info(
                "SWARM_AUTONOMY=1 — backend watch-loop owns code repair; CLI RepairWatchman not started."
            )
    except Exception as exc:
        log.warning(f"Auto-repair watchman failed to start: {exc}")

    while True:
        try:
            from organism_console._commands_opencode import mode_badge
            from organism_console.permissions import auto_mode as _perm_auto

            cwd = Path.cwd().name
            branch = ""
            try:
                _br = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=Path(__file__).parent.parent.resolve(),
                    timeout=5,
                )
                if _br.returncode == 0 and _br.stdout.strip():
                    branch = _br.stdout.strip()
            except Exception:
                pass
            branch_str = f"[bold green]{branch}[/bold green] " if branch else ""
            auto_ind = " [dim]auto[/dim]" if _perm_auto() else ""
            prompt_str = f"{mode_badge(ctx.active_agent)}{auto_ind} {branch_str}[bold bright_black]{cwd}[/bold bright_black] >>> "
            cmd_line = ctx.console.input(prompt_str).strip()

            if not cmd_line:
                continue

            cmd_ctx = build_command_context()

            execute_prompt = registry.handle_line(cmd_line, cmd_ctx)
            if execute_prompt:
                current_context_tokens = estimate_tokens(
                    execute_prompt + json.dumps(ctx.history)
                )
                if current_context_tokens > 15000 and len(ctx.history) > 12:
                    ctx.console.print(
                        "[dim]Context pressure warning: tokens exceed 15000. Auto-truncating oldest history...[/dim]"
                    )
                    keep = min(10, len(ctx.history) - 2)
                    truncate_idx = len(ctx.history) - keep
                    # BUG FIX: Add bounds check before indexing to prevent IndexError
                    if (
                        0 < truncate_idx < len(ctx.history)
                        and ctx.history[truncate_idx].get("role") == "user"
                        and "Result:"
                        in str(ctx.history[truncate_idx].get("content", ""))
                    ):
                        truncate_idx += 1
                    truncate_idx = max(1, min(truncate_idx, len(ctx.history) - 1))
                    # COMPRESS FIX: /compress now preserves the system message correctly
                    registry.handle_line("/compress", cmd_ctx)

                run_agentic(ctx, execute_prompt, json_flag)

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
