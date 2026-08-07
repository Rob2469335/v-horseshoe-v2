"""Base constants and dataclasses for the MemoryBridge."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ============================================================
# MEMORY BRIDGE v12 (FINAL SWARM CORE)
# ============================================================

CHUNK_SIZE = 12
SESSION_WINDOW = 40
DEDUP_WINDOW = 300

LLAMA_GEN = "http://127.0.0.1:8080"
LLAMA_EMB = "http://127.0.0.1:8081"
LLAMA_SUMM = "http://127.0.0.1:8084"  # 0.8B dedicated summarizer
SUM_MODEL = "qwen3.5-0.8b"
EMBED_MODEL = "gte-modernbert-base-Q8_0.gguf"
VECTOR_SIZE = 768

DECAY = 180.0

FLUSH_TRIGGERS = {
    "TASK_COMPLETE", "record_failure", "AGENT_ERROR",
    # Actual event types written by agent_service_v2 / orchestrator:
    "generation_completed", "stream_completed", "tool_result", "agent_action",
    "task_completed", "generation_failed", "healing_attempt",
}
EVENT_TYPE_KEYS = ("event_type", "type", "action", "kind")


@dataclass
class Session:
    id: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    tasks: List[str] = field(default_factory=list)


@dataclass
class Bias:
    model: str
    event_type: str
    failure_rate: float
    confidence: float
    weight: float
