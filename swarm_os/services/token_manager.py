import asyncio
import logging

log = logging.getLogger(__name__)

class TokenManager:
    """Service to track token budget and usage across the system."""
    
    def __init__(self, budget: int = 500000):
        self._lock = asyncio.Lock()
        self._total_used = 0
        self._budget = budget

    async def add_usage(self, text: str) -> None:
        """Estimate tokens from text and add to total usage."""
        estimated_tokens = int(len(text) / 4) + 1
        async with self._lock:
            self._total_used += estimated_tokens

    async def get_usage(self) -> int:
        async with self._lock:
            return self._total_used

    async def get_budget(self) -> int:
        async with self._lock:
            return self._budget

    async def check_budget(self) -> None:
        """Raises ValueError if budget is exceeded."""
        async with self._lock:
            if self._total_used >= self._budget:
                raise ValueError(f"Token budget exceeded: {self._total_used} used (limit {self._budget})")

    async def is_exhausted(self) -> bool:
        """Returns True if budget is exceeded."""
        async with self._lock:
            return self._total_used >= self._budget
