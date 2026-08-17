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
        self.active_model: str = "qwen3.5-4b"
        self.execution_phase: str = "thinking"
        self.last_tool_call: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.delegation_chain: List[str] = ["coordinator"]
        self.trace_mode: bool = False
        self.mode: str = "safe"  # safe | dev
        self.history: List[Dict[str, Any]] = []
        self.command_history: List[str] = []
        self.focus_file: Optional[str] = None
        self.cloud_enabled: bool = False
        self.speech_enabled: bool = False
        self.entry_agent: Optional[str] = None
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
        self.last_provider: str = "llama.cpp"
        self.scheduled_tasks: List[Dict[str, Any]] = []
        self.checkpoints: Dict[str, Dict[str, Any]] = {}
        # opencode-parity runtime state (not persisted across restarts):
        # working-tree snapshots for /undo, and the last prompt for /redo.
        self.undo_stack: List[Dict[str, Any]] = []
        self.last_prompt: str = ""
        self.toasts_enabled: bool = True

        self.load()

    def load(self) -> None:
        if not self.session_file.exists():
            return
        try:
            with open(self.session_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            self.active_agent = data.get("active_agent") or data.get(
                "selected_agent", self.active_agent
            )

            self.active_model = data.get("active_model", self.active_model)
            self.execution_phase = data.get("execution_phase", self.execution_phase)
            self.last_tool_call = data.get("last_tool_call", self.last_tool_call)
            self.last_error = data.get("last_error", self.last_error)
            self.entry_agent = data.get("entry_agent", self.entry_agent)
            self.delegation_chain = data.get("delegation_chain", self.delegation_chain)
            self.trace_mode = data.get("trace_mode", self.trace_mode)
            self.mode = data.get("mode", self.mode)
            self.history = data.get("history", self.history)
            self.command_history = data.get("command_history", self.command_history)
            self.focus_file = data.get("focus_file", self.focus_file)
            self.cloud_enabled = data.get("cloud_enabled", self.cloud_enabled)
            self.speech_enabled = data.get("speech_enabled", self.speech_enabled)
            self.current_topic = data.get("current_topic", self.current_topic)
            self.current_summary = data.get("current_summary", self.current_summary)
            self.strategic_intent = data.get("strategic_intent", self.strategic_intent)
            self.temp = data.get("temp", self.temp)
            self.total_input_tokens = data.get(
                "total_input_tokens", self.total_input_tokens
            )
            self.total_output_tokens = data.get(
                "total_output_tokens", self.total_output_tokens
            )
            self.cloud_input_tokens = data.get(
                "cloud_input_tokens", self.cloud_input_tokens
            )
            self.cloud_output_tokens = data.get(
                "cloud_output_tokens", self.cloud_output_tokens
            )
            self.cloud_token_quota = data.get(
                "cloud_token_quota", self.cloud_token_quota
            )
            self.history_pointer = data.get("history_pointer", self.history_pointer)
            self.last_provider = data.get("last_provider", self.last_provider)
            self.scheduled_tasks = data.get("scheduled_tasks", self.scheduled_tasks)
            self.checkpoints = data.get("checkpoints", self.checkpoints)
            self.toasts_enabled = data.get("toasts_enabled", self.toasts_enabled)
        except Exception as e:
            import logging

            logging.getLogger("zenith_cli").error(f"Failed to load session state: {e}")

    def save(self, sync: bool = False) -> None:
        def _snapshot_and_serialize() -> str:
            snap = {
                k: v
                for k, v in {
                    "active_agent": self.active_agent,
                    "selected_agent": self.active_agent,
                    "active_model": self.active_model,
                    "execution_phase": self.execution_phase,
                    "last_tool_call": self.last_tool_call,
                    "last_error": self.last_error,
                    "delegation_chain": self.delegation_chain,
                    "trace_mode": self.trace_mode,
                    "mode": self.mode,
                    "history": self.history,
                    "command_history": self.command_history[-1000:]
                    if len(self.command_history) > 1000
                    else list(self.command_history),
                    "focus_file": self.focus_file,
                    "cloud_enabled": self.cloud_enabled,
                    "speech_enabled": self.speech_enabled,
                    "entry_agent": self.entry_agent,
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
                    "scheduled_tasks": self.scheduled_tasks,
                    "checkpoints": self.checkpoints,
                    "toasts_enabled": self.toasts_enabled,
                }.items()
            }
            return json.dumps(snap, indent=2)

        def _do_save():
            try:
                payload = _snapshot_and_serialize()
                self.session_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.session_file, "w", encoding="utf-8") as fh:
                    fh.write(payload)
            except Exception as e:
                import logging

                logging.getLogger("zenith_cli").error(
                    f"Failed to save session state: {e}"
                )

        if sync:
            _do_save()
        else:
            import threading

            t = threading.Thread(target=_do_save, daemon=True)
            t.start()

    def create_checkpoint(self, name: str) -> bool:
        try:
            import copy, time

            self.checkpoints[name] = {
                "history": copy.deepcopy(self.history),
                "history_pointer": self.history_pointer,
                "scheduled_tasks": copy.deepcopy(self.scheduled_tasks),
                "active_agent": self.active_agent,
                "active_model": self.active_model,
                "timestamp": time.time(),
            }
            self.save(sync=True)
            return True
        except Exception:
            return False

    def rollback_checkpoint(self, name: str) -> bool:
        if name not in self.checkpoints:
            return False
        try:
            import copy

            cp = self.checkpoints[name]
            self.history = copy.deepcopy(cp.get("history", []))
            self.history_pointer = cp.get("history_pointer", -1)
            self.scheduled_tasks = copy.deepcopy(cp.get("scheduled_tasks", []))
            if "active_agent" in cp:
                self.active_agent = cp["active_agent"]
            if "active_model" in cp:
                self.active_model = cp["active_model"]
            self.save(sync=True)
            return True
        except Exception:
            return False
