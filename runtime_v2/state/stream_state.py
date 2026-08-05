from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StreamState:
    run_id: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    current_model: Optional[str] = None
    current_provider: Optional[str] = None
    delegation_chain: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls_made: List[Dict[str, Any]] = field(default_factory=list)
    final_emitted: bool = False
