import logging
from pathlib import Path
from typing import Dict, Any
from swarm_os.capabilities.models import ChatSearchRequest, ChatSearchResponse, ChatMessageResult
from swarm_os.events.store import EventStore

logger = logging.getLogger(__name__)

class ChatSearchHandler:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        repo_root = Path(__file__).resolve().parents[2]
        events_root = repo_root / "data" / "events"
        self.store = EventStore(events_root)
        logger.info("Initialized operational ChatSearchHandler with EventStore at %s", self.store.path)

    async def execute(self, payload: ChatSearchRequest) -> ChatSearchResponse:
        query_lower = payload.query.lower().strip()
        if not query_lower:
            return ChatSearchResponse(status="success", query=payload.query, results=[])

        events = self.store.read_all()
        matched_results = []
        query_words = query_lower.split()

        for item in events:
            message = str(
                item.get("payload")
                or item.get("message")
                or item.get("content")
                or ""
            )
            if not message:
                continue

            msg_lower = message.lower()
            matches = [word for word in query_words if word in msg_lower]
            if not matches:
                continue

            timestamp = str(
                item.get("timestamp")
                or item.get("created_at")
                or item.get("time")
                or ""
            )
            sender = str(
                item.get("sender")
                or item.get("source")
                or item.get("role")
                or item.get("kind")
                or "unknown"
            )

            score = round(len(matches) / max(len(query_words), 1), 2)
            matched_results.append(
                ChatMessageResult(
                    timestamp=timestamp,
                    sender=sender,
                    message=message,
                    score=score
                )
            )

        matched_results.sort(key=lambda x: x.score, reverse=True)
        truncated_results = matched_results[:payload.max_results]

        return ChatSearchResponse(
            status="success",
            query=payload.query,
            results=truncated_results,
            message=f"Found {len(truncated_results)} matches out of {len(events)} stored events."
        )
