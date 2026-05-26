import logging
from typing import Dict

logger = logging.getLogger(__name__)

async def archive_success(agent_id: str, tool_used: str, outcome: Dict):
    """
    Only archives high-fitness outcomes to maintain memory quality.
    """
    if outcome.get("fitness", 0) > 0.5:
        # Logic to embed and store in Qdrant
        logger.info(f"Archiving high-fitness experience for {agent_id}")
    else:
        logger.debug("Outcome below fitness threshold; memory discarded.")
