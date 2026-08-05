from typing import Dict, Any
from .plugin_state import PluginState

class PluginRegistry:
    """
    PURE METADATA LAYER ONLY.
    - stores plugins
    - tracks fitness
    - does NOT influence routing decisions directly
    """

    def __init__(self):
        self._plugins: Dict[str, Any] = {}
        self._state: Dict[str, PluginState] = {}

    def register(self, name: str, plugin: Any):
        self._plugins[name] = plugin
        if name not in self._state:
            self._state[name] = PluginState(name=name)

    def get(self, name: str):
        return self._plugins.get(name)

    def state(self, name: str) -> PluginState:
        return self._state[name]

    def all_states(self):
        return self._state

