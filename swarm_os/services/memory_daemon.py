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
                        elif not pruned.get("ok", True):
                            logger.warning(
                                "Memory GC FAILED (stale refs will persist): %s",
                                pruned.get("error") or pruned,
                            )
                    except Exception as gc_exc:
                        logger.debug("memory stale-ref GC skipped: %s", gc_exc)

                    # Decision-cache GC: a cached decision that references a
                    # deleted file replays into the agent loop and sends it
                    # chasing a nonexistent path. The memory GC above misses it —
                    # the decision cache is a separate Qdrant collection.
                    try:
                        from runtime_v2.services._semantic_decision_cache import (
                            prune_stale_decisions,
                        )

                        dpruned = await prune_stale_decisions()
                        if dpruned.get("deleted"):
                            logger.info(
                                "Decision-cache GC: purged %s stale decisions",
                                dpruned["deleted"],
                            )
                        elif not dpruned.get("ok", True):
                            logger.warning(
                                "Decision-cache GC FAILED (stale decisions will "
                                "persist): %s",
                                dpruned.get("error") or dpruned,
                            )
                    except Exception as dgc_exc:
                        logger.debug("decision-cache GC skipped: %s", dgc_exc)
                except Exception as exc:
                    logger.warning("manager daemon error: %s", exc)
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
