import sys
import json
import logging
import swarm_os.bootstrap
from rich.console import Console
from rich.logging import RichHandler

from organism_console.config import SESSION_FILE, LOG_DIR
from organism_console.state_store import SessionState
from swarm_os.services.control_plane.bootstrap import build_router
from organism_console.command_registry import registry, CommandContext

from organism_console.api_client import call_api
from organism_console.ui.banner import print_banner, get_system_stats, estimate_tokens
from organism_console.ui.live_stream import stream_prompt_with_retry, stream_prompt
from organism_console.loops.autonomous import run_autonomous_goal_loop
from organism_console.loops.debate import run_debate_loop

import organism_console.commands  # Ensures @registry.register commands are loaded

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
    return ["phi4-mini:latest"]

def main():
    if len(sys.argv) > 1:
        cmd_line = " ".join(sys.argv[1:])
        cmd_ctx = CommandContext(
            state=ctx,
            console=ctx.console,
            call_api=call_api,
            run_prompt=lambda p: stream_prompt_with_retry(ctx, ctx.active_agent, p, ctx.history),
            get_system_stats=get_system_stats,
            installed_models=get_installed_models()
        )
        cmd_ctx.run_goal_loop = lambda g: run_autonomous_goal_loop(g, cmd_ctx)
        cmd_ctx.run_debate = lambda g: run_debate_loop(g, cmd_ctx)
        cmd_ctx.run_prompt_with_agent = lambda agent, prompt: stream_prompt_with_retry(ctx, agent, prompt, ctx.history)
        registry.handle_line(cmd_line, cmd_ctx)
        return 0

    print_banner(ctx)

    if not call_api("/health"):
        ctx.console.print("[bold red]✗ Backend appears offline.[/bold red] Use '/boot' (cmd line) or check logs.")

    while True:
        try:
            prompt_str = f"[bold #00ffff]Z E N I T H[/bold #00ffff] [dim]::[/dim] [bold #ff00ff]{ctx.active_agent}[/bold #ff00ff] [bold #00ffcc]>[/bold #00ffcc] "
            cmd_line = ctx.console.input(prompt_str).strip()

            if not cmd_line:
                continue

            cmd_ctx = CommandContext(
                state=ctx,
                console=ctx.console,
                call_api=call_api,
                run_prompt=lambda p: stream_prompt_with_retry(ctx, ctx.active_agent, p, ctx.history),
                get_system_stats=get_system_stats,
                installed_models=get_installed_models()
            )
            cmd_ctx.run_goal_loop = lambda g: run_autonomous_goal_loop(g, cmd_ctx)
            cmd_ctx.run_debate = lambda g: run_debate_loop(g, cmd_ctx)
            cmd_ctx.run_prompt_with_agent = lambda agent, prompt: stream_prompt_with_retry(ctx, agent, prompt, ctx.history)

            execute_prompt = registry.handle_line(cmd_line, cmd_ctx)
            if execute_prompt:
                current_context_tokens = estimate_tokens(execute_prompt + json.dumps(ctx.history))
                if current_context_tokens > 15000:
                    ctx.console.print("[dim]Context pressure warning: tokens exceed 15000. Auto-truncating oldest history...[/dim]")
                    if len(ctx.history) > 10:
                        ctx.history = ctx.history[:1] + ctx.history[-10:]
                    registry.handle_line("/compress", cmd_ctx)
                
                ctx.history = stream_prompt_with_retry(ctx, ctx.active_agent, execute_prompt, ctx.history)

        except KeyboardInterrupt:
            ctx.console.print("\\n[dim]Use '/exit' to quit properly.[/dim]")
            continue
        except EOFError:
            break
        except Exception as e:
            log.exception("Main loop exception")
            ctx.console.print(f"[bold red]Unexpected error:[/bold red] {e}")

    return 0

if __name__ == "__main__":
    sys.exit(main())

