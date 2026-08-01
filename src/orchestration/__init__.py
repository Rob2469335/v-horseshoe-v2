"""
Multi-Agent Orchestration with Circuit Breaker Middleware.

Implements dynamic multi-agent routing with:
- Circuit breaker pattern for fault tolerance
- Agent health monitoring and self-reporting
- Weighted policy graph for dynamic node selection
- Automatic failover within 2s of failure detection

Based on 2025-2026 research on agent reliability and routing patterns.
"""

from .circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerConfig
from .health_monitor import HealthMonitor, AgentHealth, HealthMetric
from .policy_graph import PolicyGraph, RoutingPolicy, PolicyNode, NodeWeight
from .router import DynamicRouter, RoutingDecision, AgentRoute
from .orchestrator import MultiAgentOrchestrator, OrchestrationConfig

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "CircuitBreakerConfig",
    "HealthMonitor",
    "AgentHealth",
    "HealthMetric",
    "PolicyGraph",
    "RoutingPolicy",
    "PolicyNode",
    "NodeWeight",
    "DynamicRouter",
    "RoutingDecision",
    "AgentRoute",
    "MultiAgentOrchestrator",
    "OrchestrationConfig",
]

__version__ = "1.0.0"