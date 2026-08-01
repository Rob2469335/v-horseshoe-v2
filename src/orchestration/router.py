"""
Dynamic Router with Circuit Breaker Middleware.

Integrates policy graph routing with circuit breaker protection
for resilient multi-agent orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TypeVar

from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState, CircuitOpenError
from .health_monitor import HealthMonitor, AgentHealth, HealthThreshold
from .policy_graph import (
    PolicyGraph,
    PolicyNode,
    RoutingPolicy,
    RoutingDecision,
    RoutingStrategy,
    NodeWeight,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RoutingMode(Enum):
    """Routing execution modes."""
    SYNCHRONOUS = "synchronous"      # Wait for result
    ASYNC_FIRE_AND_FORGET = "async_fire_and_forget"  # Don't wait
    STREAMING = "streaming"          # Stream results


@dataclass
class AgentRoute:
    """Represents a route to an agent."""
    
    agent_id: str
    node_id: str
    endpoint: str  # URL, queue name, or direct reference
    metadata: Dict[str, Any] = field(default_factory=dict)
    capabilities: Set[str] = field(default_factory=set)
    max_concurrent: int = 10
    
    # Runtime state
    active_requests: int = 0
    total_requests: int = 0
    total_latency_ms: float = 0.0
    
    def is_available(self) -> bool:
        return self.active_requests < self.max_concurrent


@dataclass
class RoutingRequest:
    """Request to be routed."""
    
    request_id: str
    task_type: str
    payload: Any
    context: Dict[str, Any] = field(default_factory=dict)
    policy_name: str = "default"
    mode: RoutingMode = RoutingMode.SYNCHRONOUS
    timeout: float = 30.0
    priority: int = 0
    
    # Callback for streaming
    stream_callback: Optional[Callable[[Any], Awaitable[None]]] = None


@dataclass
class RoutingResult:
    """Result of a routing operation."""
    
    request_id: str
    success: bool
    agent_id: Optional[str] = None
    node_id: Optional[str] = None
    result: Any = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    attempts: int = 1
    failover_used: bool = False
    failover_chain: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class CircuitBreakerMiddleware:
    """
    Circuit breaker middleware for agent routing.
    
    Wraps agent calls with circuit breaker protection,
    integrates with health monitor for automatic recovery.
    """
    
    def __init__(
        self,
        default_config: Optional[CircuitBreakerConfig] = None,
        health_monitor: Optional[HealthMonitor] = None,
    ):
        self.default_config = default_config or CircuitBreakerConfig()
        self.health_monitor = health_monitor
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
        
        # Metrics
        self._stats = defaultdict(lambda: {
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "rejections": 0,
            "fallbacks": 0,
        })
    
    def get_breaker(self, agent_id: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """Get or create circuit breaker for agent."""
        if agent_id not in self._breakers:
            self._breakers[agent_id] = CircuitBreaker(
                name=f"agent_{agent_id}",
                config=config or self.default_config,
            )
        return self._breakers[agent_id]
    
    async def call_with_protection(
        self,
        agent_id: str,
        func: Callable[..., Awaitable[T]],
        *args,
        fallback: Optional[Callable[..., Awaitable[T]]] = None,
        config: Optional[CircuitBreakerConfig] = None,
        **kwargs,
    ) -> T:
        """
        Execute function with circuit breaker protection.
        
        Args:
            agent_id: Target agent ID
            func: Async function to execute
            *args: Positional arguments
            fallback: Fallback function if circuit open or failure
            config: Custom circuit breaker config
            **kwargs: Keyword arguments
            
        Returns:
            Function result or fallback result
            
        Raises:
            CircuitOpenError: If circuit open and no fallback
            Exception: Original exception if no fallback
        """
        breaker = self.get_breaker(agent_id, config)
        stats = self._stats[agent_id]
        
        stats["calls"] += 1
        
        try:
            result = await breaker.call(func, *args, fallback=fallback, **kwargs)
            stats["successes"] += 1
            
            # Record success in health monitor
            if self.health_monitor:
                self.health_monitor.record_success(agent_id)
            
            return result
            
        except CircuitOpenError:
            stats["rejections"] += 1
            if fallback:
                stats["fallbacks"] += 1
                return await fallback(*args, **kwargs)
            raise
            
        except Exception as e:
            stats["failures"] += 1
            
            # Record failure in health monitor
            if self.health_monitor:
                self.health_monitor.record_error(agent_id, str(e))
            
            if fallback:
                stats["fallbacks"] += 1
                return await fallback(*args, **kwargs)
            raise
    
    def get_all_states(self) -> Dict[str, CircuitState]:
        """Get states of all circuit breakers."""
        return {aid: b.state for aid, b in self._breakers.items()}
    
    def get_stats(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """Get middleware statistics."""
        if agent_id:
            return self._stats.get(agent_id, {})
        return dict(self._stats)
    
    async def reset_all(self) -> None:
        """Reset all circuit breakers."""
        async with self._lock:
            for breaker in self._breakers.values():
                await breaker.reset()
            self._stats.clear()


class DynamicRouter:
    """
    Dynamic Multi-Agent Router with Circuit Breaker Middleware.
    
    Features:
    - Policy graph based routing with dynamic weights
    - Circuit breaker protection per agent
    - Health-aware load balancing
    - Automatic failover with configurable chains
    - Request tracing and metrics
    - Streaming support
    """
    
    def __init__(
        self,
        policy_graph: Optional[PolicyGraph] = None,
        health_monitor: Optional[HealthMonitor] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        default_timeout: float = 30.0,
        max_failover_attempts: int = 3,
        failover_timeout: float = 2.0,
    ):
        """
        Initialize dynamic router.
        
        Args:
            policy_graph: Policy graph for routing decisions
            health_monitor: Health monitor for agent health
            circuit_breaker_config: Default circuit breaker config
            default_timeout: Default request timeout
            max_failover_attempts: Max failover attempts
            failover_timeout: Timeout for failover attempts
        """
        self.policy_graph = policy_graph or PolicyGraph()
        self.health_monitor = health_monitor or HealthMonitor()
        self.circuit_breaker_middleware = CircuitBreakerMiddleware(
            default_config=circuit_breaker_config,
            health_monitor=self.health_monitor,
        )
        
        self.default_timeout = default_timeout
        self.max_failover_attempts = max_failover_attempts
        self.failover_timeout = failover_timeout
        
        # Agent routes registry
        self._routes: Dict[str, AgentRoute] = {}
        
        # Request tracking
        self._active_requests: Dict[str, RoutingRequest] = {}
        self._request_history: List[RoutingResult] = []
        self._max_history = 10000
        
        # Metrics
        self._metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "failover_requests": 0,
            "total_latency_ms": 0.0,
        }
        
        # Integration
        self.policy_graph.set_health_monitor(self.health_monitor)
        
        self._lock = asyncio.Lock()
        
        logger.info("Dynamic router initialized")
    
    def register_agent(
        self,
        agent_id: str,
        node_id: str,
        endpoint: str,
        capabilities: Optional[Set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_concurrent: int = 10,
    ) -> None:
        """Register an agent route."""
        route = AgentRoute(
            agent_id=agent_id,
            node_id=node_id,
            endpoint=endpoint,
            capabilities=capabilities or set(),
            metadata=metadata or {},
            max_concurrent=max_concurrent,
        )
        self._routes[agent_id] = route
        
        # Ensure node exists in policy graph
        node = self.policy_graph.get_node(node_id)
        if not node:
            node = PolicyNode(node_id=node_id, agent_ids=[agent_id])
            self.policy_graph.add_node(node)
        elif agent_id not in node.agent_ids:
            node.agent_ids.append(agent_id)
        
        logger.info(f"Registered agent: {agent_id} -> node: {node_id}")
    
    def unregister_agent(self, agent_id: str) -> bool:
        """Unregister an agent."""
        if agent_id in self._routes:
            route = self._routes.pop(agent_id)
            # Remove from policy graph node
            node = self.policy_graph.get_node(route.node_id)
            if node and agent_id in node.agent_ids:
                node.agent_ids.remove(agent_id)
            logger.info(f"Unregistered agent: {agent_id}")
            return True
        return False
    
    def get_route(self, agent_id: str) -> Optional[AgentRoute]:
        """Get agent route."""
        return self._routes.get(agent_id)
    
    async def route_request(
        self,
        request: RoutingRequest,
        handler: Callable[[AgentRoute, RoutingRequest], Awaitable[Any]],
    ) -> RoutingResult:
        """
        Route a request to an agent with failover support.
        
        Args:
            request: Routing request
            handler: Async function(route, request) -> result
            
        Returns:
            RoutingResult with outcome
        """
        start_time = time.time()
        request_id = request.request_id or str(uuid.uuid4())
        request.request_id = request_id
        
        self._metrics["total_requests"] += 1
        
        # Track active request
        self._active_requests[request_id] = request
        
        try:
            # Get routing decision
            decision = await self.policy_graph.route(request.context, request.policy_name)
            
            if not decision.success:
                return RoutingResult(
                    request_id=request_id,
                    success=False,
                    error=decision.error or "Routing failed",
                    latency_ms=(time.time() - start_time) * 1000,
                )
            
            # Try primary node
            result = await self._execute_with_failover(
                request=request,
                handler=handler,
                decision=decision,
                start_time=start_time,
            )
            
            return result
            
        finally:
            self._active_requests.pop(request_id, None)
            self._add_to_history(request_id, result if 'result' in locals() else RoutingResult(
                request_id=request_id,
                success=False,
                error="Unknown error",
                latency_ms=(time.time() - start_time) * 1000,
            ))
    
    async def _execute_with_failover(
        self,
        request: RoutingRequest,
        handler: Callable[[AgentRoute, RoutingRequest], Awaitable[Any]],
        decision: RoutingDecision,
        start_time: float,
    ) -> RoutingResult:
        """Execute request with failover chain."""
        
        # Build attempt chain: primary + failover
        attempts = []
        if decision.primary_node:
            primary_route = self._get_route_for_node(decision.primary_node)
            if primary_route:
                attempts.append(("primary", primary_route, decision.primary_node.node_id))
        
        for node in decision.failover_chain[:self.max_failover_attempts]:
            route = self._get_route_for_node(node)
            if route:
                attempts.append(("failover", route, node.node_id))
        
        if not attempts:
            return RoutingResult(
                request_id=request.request_id,
                success=False,
                error="No available routes",
                latency_ms=(time.time() - start_time) * 1000,
            )
        
        last_error = None
        failover_used = False
        failover_chain = []
        
        for attempt_type, route, node_id in attempts:
            failover_chain.append(node_id)
            
            if attempt_type == "failover":
                failover_used = True
                self._metrics["failover_requests"] += 1
            
            try:
                # Execute with circuit breaker protection
                result = await self.circuit_breaker_middleware.call_with_protection(
                    agent_id=route.agent_id,
                    func=handler,
                    route=route,
                    request=request,
                    fallback=None,
                )
                
                latency_ms = (time.time() - start_time) * 1000
                
                # Update route stats
                route.total_requests += 1
                route.total_latency_ms += latency_ms
                
                self._metrics["successful_requests"] += 1
                self._metrics["total_latency_ms"] += latency_ms
                
                return RoutingResult(
                    request_id=request.request_id,
                    success=True,
                    agent_id=route.agent_id,
                    node_id=node_id,
                    result=result,
                    latency_ms=latency_ms,
                    attempts=len(failover_chain),
                    failover_used=failover_used,
                    failover_chain=failover_chain[:-1] if failover_used else [],
                )
                
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt failed for {route.agent_id}: {e}")
                continue
        
        # All attempts failed
        latency_ms = (time.time() - start_time) * 1000
        self._metrics["failed_requests"] += 1
        
        return RoutingResult(
            request_id=request.request_id,
            success=False,
            error=str(last_error) if last_error else "All attempts failed",
            latency_ms=latency_ms,
            attempts=len(attempts),
            failover_used=failover_used,
            failover_chain=failover_chain,
        )
    
    def _get_route_for_node(self, node: PolicyNode) -> Optional[AgentRoute]:
        """Get an available route for a policy node."""
        # Try each agent in the node
        for agent_id in node.agent_ids:
            route = self._routes.get(agent_id)
            if route and route.is_available():
                return route
        return None
    
    async def route_stream(
        self,
        request: RoutingRequest,
        handler: Callable[[AgentRoute, RoutingRequest], Any],  # Async generator
    ):
        """
        Route a streaming request.
        
        Yields:
            Streaming results from the handler
        """
        request.mode = RoutingMode.STREAMING
        decision = await self.policy_graph.route(request.context, request.policy_name)
        
        if not decision.success or not decision.primary_node:
            yield {"error": decision.error or "Routing failed"}
            return
        
        route = self._get_route_for_node(decision.primary_node)
        if not route:
            yield {"error": "No available route"}
            return
        
        # Stream from handler
        try:
            async for chunk in handler(route, request):
                yield chunk
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield {"error": str(e)}
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get router metrics."""
        total = self._metrics["total_requests"]
        avg_latency = (
            self._metrics["total_latency_ms"] / self._metrics["successful_requests"]
            if self._metrics["successful_requests"] > 0 else 0
        )
        
        return {
            **self._metrics,
            "active_requests": len(self._active_requests),
            "success_rate": self._metrics["successful_requests"] / total if total > 0 else 0,
            "failover_rate": self._metrics["failover_requests"] / total if total > 0 else 0,
            "avg_latency_ms": avg_latency,
            "registered_agents": len(self._routes),
            "policy_graph": self.policy_graph.get_routing_stats(),
            "circuit_breakers": self.circuit_breaker_middleware.get_all_states(),
        }
    
    def get_recent_results(self, limit: int = 100) -> List[RoutingResult]:
        """Get recent routing results."""
        return self._request_history[-limit:]
    
    def _add_to_history(self, request_id: str, result: RoutingResult) -> None:
        """Add result to history."""
        self._request_history.append(result)
        if len(self._request_history) > self._max_history:
            self._request_history = self._request_history[-self._max_history:]
    
    async def start(self) -> None:
        """Start router background tasks."""
        await self.policy_graph.start_health_updates()
        await self.health_monitor.start_evaluation()
        logger.info("Dynamic router started")
    
    async def stop(self) -> None:
        """Stop router background tasks."""
        await self.policy_graph.stop_health_updates()
        await self.health_monitor.stop_evaluation()
        logger.info("Dynamic router stopped")
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on all components."""
        healthy_agents = self.health_monitor.get_healthy_agents()
        degraded_agents = self.health_monitor.get_degraded_agents()
        unhealthy_agents = self.health_monitor.get_unhealthy_agents()
        
        cb_states = self.circuit_breaker_middleware.get_all_states()
        open_circuits = [aid for aid, state in cb_states.items() if state == CircuitState.OPEN]
        
        return {
            "status": "healthy" if not unhealthy_agents and not open_circuits else "degraded",
            "healthy_agents": len(healthy_agents),
            "degraded_agents": len(degraded_agents),
            "unhealthy_agents": len(unhealthy_agents),
            "open_circuits": len(open_circuits),
            "active_requests": len(self._active_requests),
            "registered_agents": len(self._routes),
        }


# Convenience function for quick setup
def create_dynamic_router(
    agents: List[Dict[str, Any]],
    strategy: RoutingStrategy = RoutingStrategy.WEIGHTED_RANDOM,
) -> DynamicRouter:
    """
    Create a dynamic router with pre-configured agents.
    
    Args:
        agents: List of agent configs with keys:
            - agent_id (required)
            - node_id (optional, defaults to agent_id)
            - endpoint (required)
            - capabilities (optional)
            - priority (optional, for failover)
        strategy: Default routing strategy
        
    Returns:
        Configured DynamicRouter
    """
    router = DynamicRouter()
    
    # Create policy graph with nodes
    for agent in agents:
        agent_id = agent["agent_id"]
        node_id = agent.get("node_id", agent_id)
        
        router.register_agent(
            agent_id=agent_id,
            node_id=node_id,
            endpoint=agent["endpoint"],
            capabilities=set(agent.get("capabilities", [])),
            metadata=agent.get("metadata", {}),
            max_concurrent=agent.get("max_concurrent", 10),
        )
        
        # Set priority if provided
        if "priority" in agent:
            node = router.policy_graph.get_node(node_id)
            if node:
                node.priority = agent["priority"]
    
    # Configure default policy
    default_policy = RoutingPolicy(
        name="default",
        strategy=strategy,
        enable_failover=True,
        max_failover_attempts=3,
    )
    router.policy_graph.add_policy(default_policy)
    
    return router