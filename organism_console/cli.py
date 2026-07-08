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
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import swarm_os.bootstrap
from rich.console import Console
from rich.logging import RichHandler

from organism_console.config import SESSION_FILE, LOG_DIR, VERSION
from organism_console.state_store import SessionState
from swarm_os.services.control_plane.bootstrap import build_router
from organism_console.command_registry import registry, CommandContext

from organism_console.api_client import call_api
from organism_console.ui.banner import print_banner, get_system_stats, estimate_tokens
from organism_console.ui.live_stream import stream_prompt_with_retry, stream_prompt
from organism_console.loops.autonomous import run_autonomous_goal_loop
from organism_console.loops.debate import run_debate_loop

import organism_console.commands

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True), logging.FileHandler(LOG_DIR / "cli.log")]
)
log = logging.getLogger("zenith_cli")


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


def get_installed_models():
    try:
        resp = call_api("/status")
        if resp:
            return resp.json().get("installed_models", [])
    except Exception:
        pass

    try:
        from runtime_v2.services.model_registry import get_model
        coordinator_model, _ = get_model("coordinator")
        return [coordinator_model]
    except Exception:
        return ["danielsheep/Qwen3-Coder-30B-A3B-Instruct-1M-Unsloth:UD-IQ3_XXS"]


def build_command_context(cmd_ctx_console=None, cmd_ctx_state=None) -> CommandContext:
    """Build a CommandContext with proper closure bindings."""
    _state = cmd_ctx_state or ctx
    _console = cmd_ctx_console or ctx.console
    _installed = get_installed_models()
    
    _ctx = CommandContext(
        state=_state,
        console=_console,
        call_api=call_api,
        run_prompt=lambda p: stream_prompt_with_retry(ctx, ctx.active_agent, p, ctx.history),
        get_system_stats=get_system_stats,
        installed_models=_installed,
    )
    _ctx.run_goal_loop = lambda g, __ctx=_ctx: run_autonomous_goal_loop(g, __ctx)
    _ctx.run_debate = lambda g, __ctx=_ctx: run_debate_loop(g, __ctx)
    _ctx.run_prompt_with_agent = lambda agent, prompt, __state=ctx: stream_prompt_with_retry(__state, agent, prompt, __state.history)
    return _ctx


def print_version():
    ctx.console.print(f"[bold cyan]ZENITH CLI[/bold cyan] [dim]v{VERSION}[/dim]")
    ctx.console.print(f"[dim]Python {sys.version.split()[0]} on {sys.platform}[/dim]")


def handle_sigint(sig, frame):
    ctx.console.print("\n[dim]Use '/exit' to quit properly.[/dim]")


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
            cmd_list = [f"/{k}" for k in registry.commands.keys()
                       if k.startswith(text.lower()) or f"/{k}".startswith(text)]
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
    os.environ["SWARM_ROUTING_MODE"] = "local_only"

    # --- CLI arg parsing ---
    args = sys.argv[1:]
    if "--version" in args or "-v" in args:
        print_version()
        return 0

    # Parse --agent and --model flags
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
    if model_override:
        ctx.active_model = model_override

    signal.signal(signal.SIGINT, handle_sigint)

    # --- Single command mode ---
    if args:
        cmd_line = " ".join(args)
        cmd_ctx = build_command_context()
        registry.handle_line(cmd_line, cmd_ctx)
        return 0

    # --- Interactive REPL ---
    has_readline = setup_readline()
    print_banner(ctx)

    while True:
        try:
            agent_tag = f"[bold #00ffff]Z E N I T H[/bold #00ffff]"
            agent_name = f"[bold #ff00ff]{ctx.active_agent}[/bold #ff00ff]"
            prompt_str = f"{agent_tag} [dim]::[/dim] {agent_name} [bold #00ffcc]>[/bold #00ffcc] "
            cmd_line = ctx.console.input(prompt_str).strip()

            if not cmd_line:
                continue

            cmd_ctx = build_command_context()

            execute_prompt = registry.handle_line(cmd_line, cmd_ctx)
            if execute_prompt:
                current_context_tokens = estimate_tokens(execute_prompt + json.dumps(ctx.history))
                if current_context_tokens > 15000:
                    ctx.console.print("[dim]Context pressure warning: tokens exceed 15000. Auto-truncating oldest history...[/dim]")
                    if len(ctx.history) > 10:
                        truncate_idx = len(ctx.history) - 10
                        if ctx.history[truncate_idx].get("role") == "user" and "Result:" in str(ctx.history[truncate_idx].get("content")):
                            truncate_idx -= 1
                        truncate_idx = max(1, truncate_idx)
                        ctx.history = ctx.history[:1] + ctx.history[truncate_idx:]
                    registry.handle_line("/compress", cmd_ctx)

                ctx.history = stream_prompt_with_retry(ctx, ctx.active_agent, execute_prompt, ctx.history)

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
