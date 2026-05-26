import logging
from typing import Dict, Any
from swarm_os.capabilities.models import ChatSearchRequest, ChatSearchResponse, ChatMessageResult

logger = logging.getLogger(__name__)

class ChatSearchHandler:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._mock_archive = [
            {"timestamp": "2026-05-20 10:14:00", "sender": "system", "message": "Deploying SwarmKernel setup with 4 active organisms."},
            {"timestamp": "2026-05-21 14:22:05", "sender": "organism_0", "message": "Optimizing coding engine performance weights using pytest metrics."},
            {"timestamp": "2026-05-22 09:05:11", "sender": "user", "message": "Run evaluation cycle across general task domains."},
            {"timestamp": "2026-05-25 16:45:30", "sender": "system", "message": "Snapshot engine flushed tracking states to JSON archive folder."}
        ]
        logger.info("Initialized operational ChatSearchHandler.")

    async def execute(self, payload: ChatSearchRequest) -> ChatSearchResponse:
        query_lower = payload.query.lower().strip()
        if not query_lower:
            return ChatSearchResponse(status="success", query=payload.query, results=[])

        matched_results = []
        query_words = query_lower.split()

        for log in self._mock_archive:
            msg_lower = log["message"].lower()
            matches = [word for word in query_words if word in msg_lower]
            if matches:
                score = round(len(matches) / max(len(query_words), 1), 2)
                matched_results.append(
                    ChatMessageResult(
                        timestamp=log["timestamp"],
                        sender=log["sender"],
                        message=log["message"],
                        score=score
                    )
                )

        matched_results.sort(key=lambda x: x.score, reverse=True)
        truncated_results = matched_results[:payload.max_results]

        return ChatSearchResponse(
            status="success",
            query=payload.query,
            results=truncated_results,
            message=f"Found {len(truncated_results)} matches out of local logs."
        )
