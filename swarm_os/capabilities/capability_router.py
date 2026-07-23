from pathlib import Path
import logging
from typing import Dict, Any, Type
from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.upwork_analyzer import UpworkAnalyzerHandler
from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler
from swarm_os.capabilities.refactor import RefactorHandler
from swarm_os.capabilities.models import ModelsHandler
from swarm_os.capabilities.sandbox_repl import SandboxReplHandler
from swarm_os.capabilities.lsp_tool import LSPToolHandler

logger = logging.getLogger(__name__)

class CapabilityRouter:
    """
    Central router that maps capability names to handler instances.
    Safely handles mismatched signature layouts and routes execution calls smoothly.
    """

    HANDLER_MAP: Dict[str, Type[Any]] = {
        "chat_search": ChatSearchHandler,
        "upwork_analyzer": UpworkAnalyzerHandler,
        "vscode_automation": VSCodeAutomationHandler,
        "refactor": RefactorHandler,
        "models": ModelsHandler,
        "sandbox_repl": SandboxReplHandler,
        "code_exec": SandboxReplHandler,
        "lsp": LSPToolHandler,
    }

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._handlers: Dict[str, Any] = {}
        self._discover_all()
        logger.info("Initialized CapabilityRouter with %d registered capabilities", len(self.HANDLER_MAP))

    def _discover_capability(self, name: str) -> None:
        import importlib
        import inspect
        name_lower = name.lower().strip()
        path = Path(__file__).parent / f"{name_lower}.py"
        if path.exists():
            try:
                module = importlib.import_module(f"swarm_os.capabilities.{name_lower}")
                for member_name, member in inspect.getmembers(module, inspect.isclass):
                    if member_name.endswith("Handler") and member.__module__ == module.__name__:
                        CapabilityRouter.HANDLER_MAP[name_lower] = member
                        logger.info("Dynamically registered capability '%s' -> %s on-demand", name_lower, member_name)
                        return
            except Exception as e:
                logger.error("Failed to dynamically load capability module '%s' on-demand: %s", name_lower, e)

    def _discover_all(self) -> None:
        import importlib
        import inspect
        capabilities_dir = Path(__file__).parent
        for path in capabilities_dir.glob("*.py"):
            name = path.stem
            if name in ("__init__", "capability_router", "models"):
                continue
            name_lower = name.lower().strip()
            if name_lower in CapabilityRouter.HANDLER_MAP:
                continue
            try:
                module = importlib.import_module(f"swarm_os.capabilities.{name_lower}")
                for member_name, member in inspect.getmembers(module, inspect.isclass):
                    if member_name.endswith("Handler") and member.__module__ == module.__name__:
                        CapabilityRouter.HANDLER_MAP[name_lower] = member
                        logger.info("Dynamically registered capability '%s' -> %s", name_lower, member_name)
            except Exception as e:
                logger.error("Failed to dynamically load capability module '%s': %s", name_lower, e)

    def get_handler(self, capability_name: str) -> Any:
        """
        Get or create a handler instance, carefully mapping custom parameters.
        """
        capability_name = capability_name.lower().strip()

        if capability_name not in self.HANDLER_MAP:
            self._discover_capability(capability_name)

        if capability_name not in self.HANDLER_MAP:
            raise KeyError(
                f"Capability '{capability_name}' not registered. "
                f"Available: {list(self.HANDLER_MAP.keys())}"
            )

        if capability_name not in self._handlers:
            handler_class = self.HANDLER_MAP[capability_name]
            handler_config = self.config.get(capability_name, {})

            # Audit Check: Tailor parameters to fit the distinct handler constructors precisely
            if capability_name == "upwork_analyzer":
                self._handlers[capability_name] = handler_class(rules=handler_config or None)
            elif capability_name == "sandbox_repl":
                self._handlers[capability_name] = handler_class()
            elif capability_name == "vscode_automation":
                # Fallback to current directory string if no custom root is supplied
                default_root = str(Path(__file__).resolve().parents[1])
                root_path = handler_config.get("workspace_root", default_root) if isinstance(handler_config, dict) else default_root
                self._handlers[capability_name] = handler_class(workspace_root=root_path)
            else:
                try:
                    self._handlers[capability_name] = handler_class(config=handler_config or None)
                except TypeError:
                    self._handlers[capability_name] = handler_class()

            logger.info("Created new instance for capability '%s'", capability_name)

        return self._handlers[capability_name]

    async def execute(self, capability_name: str, payload: Any) -> Any:
        """
        Unified routing access point that normalizes underlying execution differences.
        """
        capability_name = capability_name.lower().strip()
        handler = self.get_handler(capability_name)

        logger.debug("Routing capability '%s' with payload type %s", capability_name, type(payload).__name__)

        # Audit Check: Interface wrapping to route calls to the correct method
        if hasattr(handler, "execute"):
            return await handler.execute(payload)
        elif hasattr(handler, "analyze_job"):
            return await handler.analyze_job(payload)
        else:
            raise AttributeError(f"Capability handler '{capability_name}' lacks a recognized dispatch method.")

    def list_capabilities(self) -> list[str]:
        self._discover_all()
        return list(self.HANDLER_MAP.keys())
    def _summarize(self, content: str) -> str:
        # A simple heuristic: take the first 500 characters, last 500 characters, 
        # and a count of key symbols/lines to preserve "scent" of the code.
        if len(content) < 1000:
            return content
        return f"{content[:500]}...[TRUNCATED]...{content[-500:]}"




