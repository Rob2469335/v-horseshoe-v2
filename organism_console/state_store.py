# organism_console/state_store.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionState:
    def __init__(self, session_file: Path | str) -> None:
        self.session_file = Path(session_file)
        
        # Default states
        self.active_agent: str = "coordinator"
        self.active_model: str = "qwen2.5-coder:7b"
        self.execution_phase: str = "thinking"
        self.last_tool_call: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.delegation_chain: List[str] = ["coordinator"]
        self.trace_mode: bool = False
        self.mode: str = "safe"  # safe | dev
        self.history: List[Dict[str, Any]] = []
        self.command_history: List[str] = []
        self.focus_file: Optional[str] = None
        self.cloud_enabled: bool = True
        self.current_topic: str = "Nexus Initialization"
        self.current_summary: str = "Establishing connection to Zenith Swarm OS..."
        self.strategic_intent: str = ""
        self.temp: str = "0.7"
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.cloud_input_tokens: int = 0
        self.cloud_output_tokens: int = 0
        self.cloud_token_quota: int = 100000
        self.history_pointer: int = -1
        self.last_provider: str = "ollama"

        self.load()

    def load(self) -> None:
        if not self.session_file.exists():
            return
        try:
            with open(self.session_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                
            self.active_agent = data.get("active_agent", self.active_agent)
            # Support fallback to old selected_agent key
            self.active_agent = data.get("selected_agent", self.active_agent)
            
            self.active_model = data.get("active_model", self.active_model)
            self.execution_phase = data.get("execution_phase", self.execution_phase)
            self.last_tool_call = data.get("last_tool_call", self.last_tool_call)
            self.last_error = data.get("last_error", self.last_error)
            self.delegation_chain = data.get("delegation_chain", self.delegation_chain)
            self.trace_mode = data.get("trace_mode", self.trace_mode)
            self.mode = data.get("mode", self.mode)
            self.history = data.get("history", self.history)
            self.command_history = data.get("command_history", self.command_history)
            self.focus_file = data.get("focus_file", self.focus_file)
            self.cloud_enabled = data.get("cloud_enabled", self.cloud_enabled)
            self.current_topic = data.get("current_topic", self.current_topic)
            self.current_summary = data.get("current_summary", self.current_summary)
            self.strategic_intent = data.get("strategic_intent", self.strategic_intent)
            self.temp = data.get("temp", self.temp)
            self.total_input_tokens = data.get("total_input_tokens", self.total_input_tokens)
            self.total_output_tokens = data.get("total_output_tokens", self.total_output_tokens)
            self.cloud_input_tokens = data.get("cloud_input_tokens", self.cloud_input_tokens)
            self.cloud_output_tokens = data.get("cloud_output_tokens", self.cloud_output_tokens)
            self.cloud_token_quota = data.get("cloud_token_quota", self.cloud_token_quota)
            self.history_pointer = data.get("history_pointer", self.history_pointer)
            self.last_provider = data.get("last_provider", self.last_provider)
        except Exception as e:
            import logging
            logging.getLogger("zenith_cli").error(f"Failed to load session state: {e}")

    def save(self) -> None:
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as fh:
                json.dump({
                    "active_agent": self.active_agent,
                    "selected_agent": self.active_agent,  # keep for compatibility
                    "active_model": self.active_model,
                    "execution_phase": self.execution_phase,
                    "last_tool_call": self.last_tool_call,
                    "last_error": self.last_error,
                    "delegation_chain": self.delegation_chain,
                    "trace_mode": self.trace_mode,
                    "mode": self.mode,
                    "history": self.history,
                    "command_history": self.command_history,
                    "focus_file": self.focus_file,
                    "cloud_enabled": self.cloud_enabled,
                    "current_topic": self.current_topic,
                    "current_summary": self.current_summary,
                    "strategic_intent": self.strategic_intent,
                    "temp": self.temp,
                    "total_input_tokens": self.total_input_tokens,
                    "total_output_tokens": self.total_output_tokens,
                    "cloud_input_tokens": self.cloud_input_tokens,
                    "cloud_output_tokens": self.cloud_output_tokens,
                    "cloud_token_quota": self.cloud_token_quota,
                    "last_provider": self.last_provider,
                    "history_pointer": self.history_pointer,
                }, fh, indent=2, default=str)
        except Exception as e:
            import logging
            logging.getLogger("zenith_cli").error(f"Failed to save session state: {e}")
