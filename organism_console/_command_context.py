"""Command context data class."""

from typing import Any, Callable, Dict, List, Optional
from rich.console import Console


class CommandContext:
    def __init__(
        self,
        state: Any,
        console: Console,
        call_api: Callable[[str, str, Optional[Any], bool], Any],
        run_prompt: Callable[[str], None],
        get_system_stats: Callable[[], Dict[str, Any]],
        installed_models: List[str],
        run_goal_loop: Optional[Callable[[str], None]] = None,
        run_debate: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.state = state
        self.console = console
        self.call_api = call_api
        self.run_prompt = run_prompt
        self.get_system_stats = get_system_stats
        self.installed_models = installed_models
        self.run_goal_loop = run_goal_loop
        self.run_debate = run_debate
        self.run_prompt_with_agent: Optional[Callable[[str, str, Any], Any]] = None
