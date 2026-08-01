"""
Circuit Breaker Implementation for Agent Fault Tolerance.

Implements the circuit breaker pattern with:
- Three states: CLOSED, OPEN, HALF_OPEN
- Configurable failure thresholds and timeouts
- Automatic state transitions
- Metrics collection for monitoring
- Thread-safe async operation
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing recovery, limited requests allowed


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    
    # Failure threshold to open circuit
    failure_threshold: int = 5
    
    # Success threshold in half-open to close circuit
    success_threshold: int = 2
    
    # Timeout before attempting recovery (seconds)
    timeout: float = 30.0
    
    # Maximum time in half-open state before forcing open (seconds)
    half_open_max_time: float = 60.0
    
    # Expected exception types that count as failures
    expected_exceptions: tuple = (Exception,)
    
    # Exceptions that should NOT trigger circuit breaker
    excluded_exceptions: tuple = ()
    
    # Minimum requests before evaluating failure rate
    minimum_requests: int = 10
    
    # Failure rate threshold (0.0 - 1.0) to open circuit
    failure_rate_threshold: float = 0.5
    
    # Enable/disable circuit breaker
    enabled: bool = True
    
    # Callback when state changes
    on_state_change: Optional[Callable[['CircuitBreaker', CircuitState, CircuitState], None]] = None


@dataclass
class CircuitMetrics:
    """Metrics for circuit breaker monitoring."""
    
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0
    circuit_opened_count: int = 0
    circuit_closed_count: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    last_state_change: Optional[float] = None
    current_state: CircuitState = CircuitState.CLOSED
    
    # Sliding window for failure rate calculation
    recent_results: deque = field(default_factory=lambda: deque(maxlen=100))
    
    @property
    def failure_rate(self) -> float:
        """Calculate current failure rate."""
        if not self.recent_results:
            return 0.0
        failures = sum(1 for r in self.recent_results if not r)
        return failures / len(self.recent_results)
    
    @property
    def success_rate(self) -> float:
        """Calculate current success rate."""
        return 1.0 - self.failure_rate
    
    def record_success(self) -> None:
        """Record a successful request."""
        self.total_requests += 1
        self.successful_requests += 1
        self.recent_results.append(True)
        self.last_success_time = time.time()
    
    def record_failure(self) -> None:
        """Record a failed request."""
        self.total_requests += 1
        self.failed_requests += 1
        self.recent_results.append(False)
        self.last_failure_time = time.time()
    
    def record_rejection(self) -> None:
        """Record a rejected request (circuit open)."""
        self.rejected_requests += 1


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading failures.
    
    State transitions:
    - CLOSED -> OPEN: When failure threshold exceeded
    - OPEN -> HALF_OPEN: After timeout expires
    - HALF_OPEN -> CLOSED: When success threshold met
    - HALF_OPEN -> OPEN: When any failure occurs
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Unique identifier for this circuit breaker
            config: Configuration (uses defaults if not provided)
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._metrics = CircuitMetrics()
        self._lock = asyncio.Lock()
        self._half_open_start: Optional[float] = None
        self._consecutive_successes = 0
        self._consecutive_failures = 0
        self._last_state_change = time.time()
        
        logger.info(f"Circuit breaker '{name}' initialized: {self.config}")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state
    
    @property
    def metrics(self) -> CircuitMetrics:
        """Get circuit breaker metrics."""
        self._metrics.current_state = self._state
        return self._metrics
    
    @property
    def is_available(self) -> bool:
        """Check if circuit allows requests."""
        if not self.config.enabled:
            return True
        
        if self._state == CircuitState.CLOSED:
            return True
        
        if self._state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if time.time() - self._last_state_change >= self.config.timeout:
                return True  # Will transition to half-open on next request
            return False
        
        if self._state == CircuitState.HALF_OPEN:
            return True
        
        return False
    
    async def call(
        self,
        func: Callable[..., T],
        *args,
        fallback: Optional[Callable[..., T]] = None,
        **kwargs,
    ) -> T:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Async function to execute
            *args: Positional arguments for func
            fallback: Optional fallback function when circuit is open
            **kwargs: Keyword arguments for func
            
        Returns:
            Result of func or fallback
            
        Raises:
            CircuitOpenError: If circuit is open and no fallback
            Exception: Any exception from func
        """
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_state_change >= self.config.timeout:
                    await self._transition_to_half_open()
                else:
                    self._metrics.record_rejection()
                    if fallback:
                        logger.debug(f"Circuit '{self.name}' OPEN, using fallback")
                        return await fallback(*args, **kwargs) if inspect.iscoroutinefunction(fallback) else fallback(*args, **kwargs)
                    raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
            
            # In HALF_OPEN, only allow limited concurrent requests
            if self._state == CircuitState.HALF_OPEN:
                # Track half-open duration
                if self._half_open_start and time.time() - self._half_open_start > self.config.half_open_max_time:
                    await self._transition_to_open()
                    self._metrics.record_rejection()
                    if fallback:
                        return await fallback(*args, **kwargs) if inspect.iscoroutinefunction(fallback) else fallback(*args, **kwargs)
                    raise CircuitOpenError(f"Circuit '{self.name}' HALF_OPEN timeout")
        
        # Execute the function
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            await self._on_success()
            return result
            
        except self.config.excluded_exceptions:
            # Excluded exceptions don't affect circuit
            raise
            
        except self.config.expected_exceptions as e:
            await self._on_failure(e)
            if fallback:
                logger.debug(f"Circuit '{self.name}' failure, using fallback: {e}")
                return await fallback(*args, **kwargs) if inspect.iscoroutinefunction(fallback) else fallback(*args, **kwargs)
            raise
    
    async def _on_success(self) -> None:
        """Handle successful request."""
        async with self._lock:
            self._metrics.record_success()
            self._consecutive_failures = 0
            
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.config.success_threshold:
                    await self._transition_to_closed()
    
    async def _on_failure(self, exception: Exception) -> None:
        """Handle failed request."""
        async with self._lock:
            self._metrics.record_failure()
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                await self._transition_to_open()
            
            elif self._state == CircuitState.CLOSED:
                # Check if we should open the circuit
                if self._should_open_circuit():
                    await self._transition_to_open()
    
    def _should_open_circuit(self) -> bool:
        """Determine if circuit should open based on failure rate."""
        # Need minimum requests before evaluating
        if self._metrics.total_requests < self.config.minimum_requests:
            return self._consecutive_failures >= self.config.failure_threshold
        
        # Check failure rate
        return self._metrics.failure_rate >= self.config.failure_rate_threshold
    
    async def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        old_state = self._state
        self._state = CircuitState.OPEN
        self._last_state_change = time.time()
        self._metrics.circuit_opened_count += 1
        self._half_open_start = None
        
        logger.warning(f"Circuit '{self.name}' OPENED (failures: {self._consecutive_failures}, rate: {self._metrics.failure_rate:.2%})")
        
        if self.config.on_state_change:
            try:
                self.config.on_state_change(self, old_state, self._state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
    
    async def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state."""
        old_state = self._state
        self._state = CircuitState.HALF_OPEN
        self._last_state_change = time.time()
        self._half_open_start = time.time()
        self._consecutive_successes = 0
        
        logger.info(f"Circuit '{self.name}' HALF_OPEN (testing recovery)")
        
        if self.config.on_state_change:
            try:
                self.config.on_state_change(self, old_state, self._state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
    
    async def _transition_to_closed(self) -> None:
        """Transition to CLOSED state."""
        old_state = self._state
        self._state = CircuitState.CLOSED
        self._last_state_change = time.time()
        self._metrics.circuit_closed_count += 1
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._half_open_start = None
        
        logger.info(f"Circuit '{self.name}' CLOSED (recovered)")
        
        if self.config.on_state_change:
            try:
                self.config.on_state_change(self, old_state, self._state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
    
    async def force_open(self) -> None:
        """Manually force circuit to OPEN state."""
        async with self._lock:
            await self._transition_to_open()
    
    async def force_close(self) -> None:
        """Manually force circuit to CLOSED state (reset)."""
        async with self._lock:
            await self._transition_to_closed()
    
    async def reset(self) -> None:
        """Reset circuit breaker to initial state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._metrics = CircuitMetrics()
            self._consecutive_successes = 0
            self._consecutive_failures = 0
            self._half_open_start = None
            self._last_state_change = time.time()
            logger.info(f"Circuit '{self.name}' RESET")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status for monitoring."""
        return {
            "name": self.name,
            "state": self._state.value,
            "enabled": self.config.enabled,
            "metrics": {
                "total_requests": self._metrics.total_requests,
                "successful_requests": self._metrics.successful_requests,
                "failed_requests": self._metrics.failed_requests,
                "rejected_requests": self._metrics.rejected_requests,
                "failure_rate": self._metrics.failure_rate,
                "circuit_opened_count": self._metrics.circuit_opened_count,
                "circuit_closed_count": self._metrics.circuit_closed_count,
                "last_failure_time": self._metrics.last_failure_time,
                "last_success_time": self._metrics.last_success_time,
                "last_state_change": self._last_state_change,
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
                "failure_rate_threshold": self.config.failure_rate_threshold,
            }
        }


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and request is rejected."""
    pass


class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> CircuitBreaker:
        """Get existing or create new circuit breaker."""
        async with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]
    
    async def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        return self._breakers.get(name)
    
    async def remove(self, name: str) -> bool:
        """Remove circuit breaker."""
        async with self._lock:
            if name in self._breakers:
                del self._breakers[name]
                return True
            return False
    
    async def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all circuit breakers."""
        return {name: cb.get_status() for name, cb in self._breakers.items()}
    
    async def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for cb in self._breakers.values():
            await cb.reset()


# Global registry instance
_global_registry = CircuitBreakerRegistry()


async def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """Get circuit breaker from global registry."""
    return await _global_registry.get_or_create(name, config)


async def get_all_circuit_breakers() -> Dict[str, Dict[str, Any]]:
    """Get status of all circuit breakers."""
    return await _global_registry.get_all_status()