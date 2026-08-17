import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swarm_os.memory.memory_bridge import MemoryBridge

logger = logging.getLogger(__name__)


class MemoryDaemon:
    def __init__(self, memory_bridge: "MemoryBridge", interval_seconds: float = 300.0):
        self.memory_bridge = memory_bridge
        self.interval_seconds = interval_seconds

    async def start(self) -> None:
        """Memory Manager Daemon that actively synthesizes core memory blocks and pages out to Archival Qdrant."""
        try:
            # BUG FIX: Wait for Qdrant and Embedding services to fully boot before
            # starting the first consolidation/graph_rag pass.
            await asyncio.sleep(15.0)
            while True:
                try:
                    # Page out memory
                    consolidated = await self.memory_bridge.consolidate_memories()
                    if consolidated:
                        logger.info(
                            "Memory Manager Daemon: Successfully synthesized core memory and paged raw logs to Archival Memory (Qdrant)."
                        )

                    # Update graph clusters
                    await self.memory_bridge.cluster_graph_rag()
                except Exception as exc:
                    logger.warning("manager daemon error: %s", exc)
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
