import logging
from typing import Dict, Any, Type
from swarm_os.capabilities.chat_search import ChatSearchHandler
from swarm_os.capabilities.upwork_analyzer import UpworkAnalyzerHandler
from swarm_os.capabilities.vscode_automation import VSCodeAutomationHandler

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
    }

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._handlers: Dict[str, Any] = {}
        logger.info("Initialized CapabilityRouter with %d registered capabilities", len(self.HANDLER_MAP))

    def get_handler(self, capability_name: str) -> Any:
        """
        Get or create a handler instance, carefully mapping custom parameters.
        """
        capability_name = capability_name.lower().strip()

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
            elif capability_name == "vscode_automation":
                # Fallback to current directory string if no custom root is supplied
                root_path = handler_config.get("workspace_root", ".") if isinstance(handler_config, dict) else "."
                self._handlers[capability_name] = handler_class(workspace_root=root_path)
            else:
                self._handlers[capability_name] = handler_class(config=handler_config or None)

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
        return list(self.HANDLER_MAP.keys())
    def _summarize(self, content: str) -> str:
        # A simple heuristic: take the first 500 characters, last 500 characters, 
        # and a count of key symbols/lines to preserve "scent" of the code.
        if len(content) < 1000:
            return content
        return f"{content[:500]}...[TRUNCATED]...{content[-500:]}"
