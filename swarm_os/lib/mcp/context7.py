from __future__ import annotations

import logging
from typing import Any, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)

# Simple in-memory storage for context during the session life
_CONTEXT_STORE: Dict[str, List[Dict[str, Any]]] = {}


async def context7_handler(params: Dict[str, Any], trace_hook=None) -> Dict[str, Any]:
    """
    Handles context management for long-running sessions.
    Provides storage and retrieval for session-specific context snapshots.
    """
    operation = params.get("operation", "read")
    session_id = str(params.get("session_id", "default"))

    if session_id not in _CONTEXT_STORE:
        _CONTEXT_STORE[session_id] = []

    try:
        if operation == "write":
            content = params.get("content")
            if content is None:
                return {"ok": False, "error": "Content is required for write operation"}

            metadata = params.get("metadata", {})
            entry = {
                "timestamp": datetime.now().isoformat(),
                "content": content,
                "metadata": metadata,
            }

            _CONTEXT_STORE[session_id].append(entry)

            # Keep only the last 50 entries to prevent memory bloat
            if len(_CONTEXT_STORE[session_id]) > 50:
                _CONTEXT_STORE[session_id] = _CONTEXT_STORE[session_id][-50:]

            if trace_hook:
                trace_hook(
                    "context7_write",
                    {
                        "session_id": session_id,
                        "entries": len(_CONTEXT_STORE[session_id]),
                    },
                )

            return {"ok": True, "session_id": session_id, "message": "Context saved"}

        elif operation == "read":
            limit = int(params.get("limit", 10))
            entries = _CONTEXT_STORE[session_id][-limit:]

            return {
                "ok": True,
                "session_id": session_id,
                "entries": entries,
                "total_entries": len(_CONTEXT_STORE[session_id]),
            }

        elif operation == "clear":
            _CONTEXT_STORE[session_id] = []
            return {"ok": True, "session_id": session_id, "message": "Context cleared"}

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        logger.exception("Context7 tool error")
        return {"ok": False, "error": str(e)}
