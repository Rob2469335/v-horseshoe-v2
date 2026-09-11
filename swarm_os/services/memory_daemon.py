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

                    # Self-purge stale file-reference memories (2026-09-10): a
                    # "File not found: <path>" reflection whose file has since
                    # been deleted keeps re-entering agent context as if current
                    # (fed fabricated findings into codebase-analysis finals).
                    # TTL pruning misses it (recent memory); this drops it.
                    try:
                        from runtime_v2.services.memory_core import (
                            prune_stale_file_memories,
                        )

                        pruned = await asyncio.to_thread(
                            prune_stale_file_memories
                        )
                        if pruned.get("deleted"):
                            logger.info(
                                "Memory GC: purged %s stale file-reference memories",
                                pruned["deleted"],
                            )
                    except Exception as gc_exc:
                        logger.debug("memory stale-ref GC skipped: %s", gc_exc)
                except Exception as exc:
                    logger.warning("manager daemon error: %s", exc)
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
