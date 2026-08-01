"""Command registry — orchestrates CLI command registration and execution.

Responsibilities:
  - CommandRegistry class + global `registry` instance
  - run_syntax_checks() helper for AST validation
  - Natural language routing via keyword matching & LLM
  - All command function registrations (imported from _commands_*)

This file is deliberately thin — each command group lives in its own module:
  _commands_system.py  — /help, /status, /trace, /cloud, /tools, /mcp, /routing, ...
  _commands_dev.py     — /diff, /commit, /branch, /debug, /patch, /impact, ...
  _commands_ai.py      — /heal, /upgrade, /goal, /vote, /memory, /simulation, ...
  _command_context.py  — CommandContext class
  _command_routing.py  — route_natural_language_keywords, classify_intent_with_llm
  _command_deps.py     — ImportVisitor, dependency analysis for /impact
"""

import ast
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from organism_console._command_context import CommandContext
from organism_console._command_routing import route_natural_language_keywords, classify_intent_with_llm


def run_syntax_checks(root: Path) -> tuple[bool, str]:
    try:
        git_diff = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=root, timeout=5
        )
        if git_diff.returncode == 0:
            modified_files = [line.strip() for line in git_diff.stdout.splitlines() if line.strip()]
            for f in modified_files:
                file_path = root / f
                if file_path.suffix == ".py" and file_path.exists():
                    content = ""
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="ignore")
                        ast.parse(content, filename=str(file_path))
                    except SyntaxError as exc:
                        lines = content.splitlines()
                        err_line = exc.lineno
                        context_lines = []
                        if err_line:
                            start = max(0, err_line - 4)
                            end = min(len(lines), err_line + 3)
                            for idx in range(start, end):
                                prefix = ">>> " if idx + 1 == err_line else "    "
                                context_lines.append(f"{prefix}{idx+1}: {lines[idx]}")
                        return False, f"File: {f}\nError: {exc.msg} at line {exc.lineno}\nCode Context:\n```python\n{chr(10).join(context_lines)}\n```"
    except Exception as e:
        return False, f"Syntax checks crashed: {e}"
    return True, ""


class CommandRegistry:
    def __init__(self) -> None:
        self.commands: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, aliases: Optional[List[str]] = None) -> Callable:
        def decorator(func: Callable) -> Callable:
            cmd_info = {"func": func, "description": description, "aliases": aliases or []}
            self.commands[name] = cmd_info
            for alias in cmd_info["aliases"]:
                self.commands[alias] = cmd_info
            return func
        return decorator

    def handle_line(self, line: str, ctx: CommandContext) -> Optional[str]:
        raw = line.strip()
        if not raw:
            return None

        cmd_name = None
        args = []
        if not raw.startswith("/"):
            cmd_name, args = route_natural_language_keywords(raw)
            if not cmd_name:
                cmd_name, args = classify_intent_with_llm(raw, ctx)
            if cmd_name:
                if cmd_name == "chat":
                    return raw
                if cmd_name in self.commands:
                    ctx.console.print(f"[bold cyan]Auto-Routing intent to command: [green]/{cmd_name} {' '.join(args)}[/green][/bold cyan]")
                    ctx.state.command_history.append(f"/{cmd_name} {' '.join(args)}")
                    ctx.state.save()
                    try:
                        return self.commands[cmd_name]["func"](ctx, args)
                    except Exception as e:
                        ctx.console.print(f"[bold red]Command failed:[/bold red] {e}")
                        ctx.state.last_error = str(e)
                        ctx.state.save()
                        return None
                else:
                    ctx.console.print(f"[bold red]Unknown command:[/bold red] /{cmd_name}. Type `/help` to list commands.")
                    return None
            else:
                return raw

        parts = raw.split()
        cmd_name = parts[0][1:].lower()
        args = parts[1:]
        ctx.state.command_history.append(raw)
        ctx.state.save()

        if cmd_name not in self.commands:
            ctx.console.print(f"[bold red]Unknown command:[/bold red] /{cmd_name}. Type `/help` to list commands.")
            return None

        try:
            return self.commands[cmd_name]["func"](ctx, args)
        except Exception as e:
            ctx.console.print(f"[bold red]Command failed:[/bold red] {e}")
            ctx.state.last_error = str(e)
            ctx.state.save()
            return None


registry = CommandRegistry()

def _ensure_commands_loaded() -> None:
    """Lazy-import command modules to avoid circular imports.
    Each _commands_*.py imports `registry` from this module — importing
    them at module top level creates a circular dependency that is fragile
    and can cause ImportError on import order changes."""
    if not getattr(_ensure_commands_loaded, "_loaded", False):
        _ensure_commands_loaded._loaded = True
        from organism_console import _commands_system  # noqa: F401
        from organism_console import _commands_dev     # noqa: F401
        from organism_console import _commands_ai      # noqa: F401


# Restore module-level import side-effects for backward compatibility
_ensure_commands_loaded()
