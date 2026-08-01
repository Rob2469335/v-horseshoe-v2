"""
Weighted Policy Graph for Dynamic Multi-Agent Routing.

Implements a policy graph based on 2025-2026 research on agent reliability:
- Nodes represent agents or agent groups
- Edges represent routing policies with weights
- Dynamic weight adjustment based on health metrics
- Automatic failover and load balancing
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class RoutingStrategy(Enum):
    """Routing strategies for policy graph."""
    WEIGHTED_RANDOM = "weighted_random"      # Weighted by health/score
    LEAST_CONNECTIONS = "least_connections"   # Route to least busy
    FASTEST_RESPONSE = "fastest_response"     # Route to lowest latency
    HIGHEST_SUCCESS = "highest_success"       # Route to highest success rate
    ROUND_ROBIN = "round_robin"               # Simple round robin
    PRIORITY_FAILOVER = "priority_failover"   # Primary -> fallback chain


@dataclass
class NodeWeight:
    """Weight configuration for a policy node."""
    
    # Base static weight
    base_weight: float = 1.0
    
    # Health factor (0.0 - 1.0, multiplied by health score)
    health_factor: float = 0.5
    
    # Latency factor (lower latency = higher weight)
    latency_factor: float = 0.3
    
    # Success rate factor
    success_factor: float = 0.2
    
    # Load factor (lower load = higher weight)
    load_factor: float = 0.1
    
    # Minimum weight (never go below this)
    min_weight: float = 0.01
    
    # Maximum weight (cap)
    max_weight: float = 10.0
    
    def calculate(
        self,
        health_score: float,      # 0.0 - 1.0
        latency_ms: float,        # milliseconds
        success_rate: float,      # 0.0 - 1.0
        load: float,              # 0.0 - 1.0 (queue depth / capacity)
    ) -> float:
        """Calculate effective weight."""
        # Normalize latency (assume max 10s)
        latency_score = max(0.0, 1.0 - (latency_ms / 10000.0))
        
        # Normalize load (invert: lower load = higher score)
        load_score = max(0.0, 1.0 - load)
        
        weight = (
            self.base_weight +
            self.health_factor * health_score +
            self.latency_factor * latency_score +
            self.success_factor * success_rate +
            self.load_factor * load_score
        )
        
        return max(self.min_weight, min(self.max_weight, weight))


@dataclass
class PolicyNode:
    """Node in the policy graph representing an agent or agent group."""
    
    node_id: str
    agent_ids: List[str] = field(default_factory=list)
    
    # Node metadata
    name: str = ""
    description: str = ""
    tags: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Weight configuration
    weight_config: NodeWeight = field(default_factory=NodeWeight)
    
    # Runtime state
    enabled: bool = True
    current_weight: float = 1.0
    last_selected: float = 0.0
    selection_count: int = 0
    
    # Health data (updated from health monitor)
    health_score: float = 1.0
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    load: float = 0.0
    circuit_breaker_state: str = "closed"
    
    # Priority for failover chain (lower = higher priority)
    priority: int = 0
    
    def __post_init__(self):
        if not self.name:
            self.name = self.node_id
    
    def update_health(
        self,
        health_score: float,
        avg_latency_ms: float,
        success_rate: float,
        load: float,
        circuit_breaker_state: str,
    ) -> None:
        """Update health metrics and recalculate weight."""
        self.health_score = health_score
        self.avg_latency_ms = avg_latency_ms
        self.success_rate = success_rate
        self.load = load
        self.circuit_breaker_state = circuit_breaker_state
        
        if self.enabled and circuit_breaker_state != "open":
            self.current_weight = self.weight_config.calculate(
                health_score=health_score,
                latency_ms=avg_latency_ms,
                success_rate=success_rate,
                load=load,
            )
        else:
            self.current_weight = self.weight_config.min_weight
    
    def is_available(self) -> bool:
        """Check if node is available for routing."""
        return self.enabled and self.circuit_breaker_state != "open"


@dataclass
class RoutingPolicy:
    """Routing policy configuration."""
    
    name: str
    strategy: RoutingStrategy = RoutingStrategy.WEIGHTED_RANDOM
    
    # Node selection
    allowed_nodes: Optional[Set[str]] = None
    excluded_nodes: Set[str] = field(default_factory=set)
    required_tags: Set[str] = field(default_factory=set)
    excluded_tags: Set[str] = field(default_factory=set)
    
    # Failover
    enable_failover: bool = True
    max_failover_attempts: int = 3
    failover_timeout_ms: float = 2000.0
    
    # Load balancing
    sticky_sessions: bool = False
    session_affinity_key: str = "session_id"
    
    # Custom scoring function
    custom_scorer: Optional[Callable[[PolicyNode, Dict[str, Any]], float]] = None


class PolicyGraph:
    """
    Weighted Policy Graph for Dynamic Agent Routing.
    
    Maintains a graph of policy nodes with dynamic weights based on
    real-time health metrics. Supports multiple routing strategies
    and automatic failover.
    """
    
    def __init__(
        self,
        default_policy: Optional[RoutingPolicy] = None,
        health_update_interval: float = 5.0,
    ):
        """
        Initialize policy graph.
        
        Args:
            default_policy: Default routing policy
            health_update_interval: Interval for weight recalculation
        """
        self._nodes: Dict[str, PolicyNode] = {}
        self._edges: Dict[str, Dict[str, float]] = defaultdict(dict)  # node_id -> {target_id: weight}
        self.default_policy = default_policy or RoutingPolicy(
            name="default",
            strategy=RoutingStrategy.WEIGHTED_RANDOM,
        )
        self._policies: Dict[str, RoutingPolicy] = {"default": self.default_policy}
        
        # Health monitor integration
        self._health_monitor = None
        self._health_update_interval = health_update_interval
        self._update_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Session affinity
        self._session_affinity: Dict[str, str] = {}  # session_key -> node_id
        
        # Metrics
        self._routing_metrics: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        
        self._lock = asyncio.Lock()
        
        logger.info("Policy graph initialized")
    
    def set_health_monitor(self, health_monitor) -> None:
        """Set health monitor for automatic weight updates."""
        self._health_monitor = health_monitor
    
    def add_node(self, node: PolicyNode) -> None:
        """Add a policy node to the graph."""
        self._nodes[node.node_id] = node
        logger.info(f"Added policy node: {node.node_id} ({len(node.agent_ids)} agents)")
    
    def remove_node(self, node_id: str) -> bool:
        """Remove a policy node."""
        if node_id in self._nodes:
            del self._nodes[node_id]
            # Remove edges
            self._edges.pop(node_id, None)
            for edges in self._edges.values():
                edges.pop(node_id, None)
            logger.info(f"Removed policy node: {node_id}")
            return True
        return False
    
    def get_node(self, node_id: str) -> Optional[PolicyNode]:
        """Get node by ID."""
        return self._nodes.get(node_id)
    
    def add_edge(self, from_node: str, to_node: str, weight: float = 1.0) -> None:
        """Add directed edge between nodes (for failover chains)."""
        if from_node in self._nodes and to_node in self._nodes:
            self._edges[from_node][to_node] = weight
    
    def add_policy(self, policy: RoutingPolicy) -> None:
        """Add a named routing policy."""
        self._policies[policy.name] = policy
    
    def get_policy(self, name: str) -> Optional[RoutingPolicy]:
        """Get policy by name."""
        return self._policies.get(name)
    
    def _filter_nodes(
        self,
        policy: RoutingPolicy,
        context: Dict[str, Any],
    ) -> List[PolicyNode]:
        """Filter nodes based on policy constraints."""
        candidates = []
        
        for node in self._nodes.values():
            if not node.is_available():
                continue
            
            if policy.allowed_nodes and node.node_id not in policy.allowed_nodes:
                continue
            
            if node.node_id in policy.excluded_nodes:
                continue
            
            if policy.required_tags and not policy.required_tags.issubset(node.tags):
                continue
            
            if policy.excluded_tags and policy.excluded_tags.intersection(node.tags):
                continue
            
            # Check session affinity
            if policy.sticky_sessions:
                session_key = context.get(policy.session_affinity_key)
                if session_key and session_key in self._session_affinity:
                    if self._session_affinity[session_key] != node.node_id:
                        continue
            
            candidates.append(node)
        
        return candidates
    
    def _select_weighted_random(
        self,
        nodes: List[PolicyNode],
        policy: RoutingPolicy,
        context: Dict[str, Any],
    ) -> Optional[PolicyNode]:
        """Select node using weighted random selection."""
        if not nodes:
            return None
        
        # Apply custom scorer if provided
        weights = []
        for node in nodes:
            weight = node.current_weight
            if policy.custom_scorer:
                try:
                    weight = policy.custom_scorer(node, context)
                except Exception as e:
                    logger.warning(f"Custom scorer error for {node.node_id}: {e}")
            weights.append(max(0.0, weight))
        
        total = sum(weights)
        if total <= 0:
            return random.choice(nodes)
        
        # Weighted random selection
        r = random.random() * total
        cumsum = 0.0
        for node, weight in zip(nodes, weights):
            cumsum += weight
            if r <= cumsum:
                return node
        
        return nodes[-1]
    
    def _select_least_connections(self, nodes: List[PolicyNode]) -> Optional[PolicyNode]:
        """Select node with least connections (lowest load)."""
        if not nodes:
            return None
        return min(nodes, key=lambda n: n.load)
    
    def _select_fastest_response(self, nodes: List[PolicyNode]) -> Optional[PolicyNode]:
        """Select node with lowest latency."""
        if not nodes:
            return None
        return min(nodes, key=lambda n: n.avg_latency_ms)
    
    def _select_highest_success(self, nodes: List[PolicyNode]) -> Optional[PolicyNode]:
        """Select node with highest success rate."""
        if not nodes:
            return None
        return max(nodes, key=lambda n: n.success_rate)
    
    def _select_round_robin(self, nodes: List[PolicyNode]) -> Optional[PolicyNode]:
        """Select node using round robin."""
        if not nodes:
            return None
        return min(nodes, key=lambda n: n.selection_count)
    
    def _select_priority_failover(
        self,
        nodes: List[PolicyNode],
        policy: RoutingPolicy,
        context: Dict[str, Any],
        attempt: int = 0,
    ) -> Tuple[Optional[PolicyNode], List[PolicyNode]]:
        """Select node with priority failover chain."""
        if not nodes:
            return None, []
        
        # Sort by priority (lower = higher priority)
        sorted_nodes = sorted(nodes, key=lambda n: n.priority)
        
        # Primary node
        primary = sorted_nodes[0]
        
        # Build failover chain
        failover_chain = sorted_nodes[1:policy.max_failover_attempts]
        
        return primary, failover_chain
    
    async def route(
        self,
        context: Dict[str, Any],
        policy_name: str = "default",
    ) -> "RoutingDecision":
        """
        Route request to best available node.
        
        Args:
            context: Routing context (session_id, task_type, etc.)
            policy_name: Name of routing policy to use
            
        Returns:
            RoutingDecision with selected node and failover chain
        """
        async with self._lock:
            policy = self._policies.get(policy_name, self.default_policy)
            candidates = self._filter_nodes(policy, context)
            
            if not candidates:
                return RoutingDecision(
                    success=False,
                    error="No available nodes matching policy",
                    policy_name=policy_name,
                )
            
            # Select primary node based on strategy
            primary = None
            failover_chain = []
            
            if policy.strategy == RoutingStrategy.WEIGHTED_RANDOM:
                primary = self._select_weighted_random(candidates, policy, context)
            elif policy.strategy == RoutingStrategy.LEAST_CONNECTIONS:
                primary = self._select_least_connections(candidates)
            elif policy.strategy == RoutingStrategy.FASTEST_RESPONSE:
                primary = self._select_fastest_response(candidates)
            elif policy.strategy == RoutingStrategy.HIGHEST_SUCCESS:
                primary = self._select_highest_success(candidates)
            elif policy.strategy == RoutingStrategy.ROUND_ROBIN:
                primary = self._select_round_robin(candidates)
            elif policy.strategy == RoutingStrategy.PRIORITY_FAILOVER:
                primary, failover_chain = self._select_priority_failover(
                    candidates, policy, context
                )
            
            if not primary:
                return RoutingDecision(
                    success=False,
                    error="No node selected",
                    policy_name=policy_name,
                )
            
            # Update selection stats
            primary.last_selected = time.time()
            primary.selection_count += 1
            self._routing_metrics[policy_name][primary.node_id] += 1
            
            # Handle session affinity
            if policy.sticky_sessions:
                session_key = context.get(policy.session_affinity_key)
                if session_key:
                    self._session_affinity[session_key] = primary.node_id
            
            # Build failover chain if not already built
            if not failover_chain and policy.enable_failover:
                failover_chain = [
                    n for n in candidates
                    if n.node_id != primary.node_id
                ][:policy.max_failover_attempts]
            
            return RoutingDecision(
                success=True,
                primary_node=primary,
                failover_chain=failover_chain,
                policy_name=policy_name,
                strategy=policy.strategy,
                context=context,
            )
    
    async def update_weights_from_health(self) -> int:
        """Update all node weights from health monitor."""
        if not self._health_monitor:
            return 0
        
        updated = 0
        health_reports = self._health_monitor.get_all_health()
        
        for node in self._nodes.values():
            # Aggregate health from all agents in node
            total_score = 0.0
            total_latency = 0.0
            total_success = 0.0
            total_load = 0.0
            open_circuits = 0
            agent_count = 0
            
            for agent_id in node.agent_ids:
                health = health_reports.get(agent_id)
                if health:
                    agent_count += 1
                    total_score += 1.0 if health.is_healthy else (0.5 if health.is_degraded else 0.0)
                    total_latency += health.latency_p95
                    total_success += 1.0 - health.error_rate
                    total_load += min(1.0, health.queue_depth / 100.0)
                    if health.circuit_breaker_state == "open":
                        open_circuits += 1
            
            if agent_count > 0:
                avg_health = total_score / agent_count
                avg_latency = total_latency / agent_count
                avg_success = total_success / agent_count
                avg_load = total_load / agent_count
                cb_state = "open" if open_circuits > 0 else "closed"
                
                node.update_health(
                    health_score=avg_health,
                    avg_latency_ms=avg_latency,
                    success_rate=avg_success,
                    load=avg_load,
                    circuit_breaker_state=cb_state,
                )
                updated += 1
        
        return updated
    
    async def start_health_updates(self) -> None:
        """Start automatic weight updates from health monitor."""
        if self._running:
            return
        
        self._running = True
        self._update_task = asyncio.create_task(self._health_update_loop())
        logger.info("Policy graph health updates started")
    
    async def stop_health_updates(self) -> None:
        """Stop automatic health updates."""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        logger.info("Policy graph health updates stopped")
    
    async def _health_update_loop(self) -> None:
        """Background loop for health-based weight updates."""
        while self._running:
            try:
                await asyncio.sleep(self._health_update_interval)
                await self.update_weights_from_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health update error: {e}")
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            "nodes": {
                node_id: {
                    "weight": node.current_weight,
                    "selections": node.selection_count,
                    "health_score": node.health_score,
                    "latency_ms": node.avg_latency_ms,
                    "success_rate": node.success_rate,
                    "load": node.load,
                    "enabled": node.enabled,
                    "circuit_breaker": node.circuit_breaker_state,
                }
                for node_id, node in self._nodes.items()
            },
            "policies": {
                name: {
                    "strategy": policy.strategy.value,
                    "metrics": dict(self._routing_metrics.get(name, {})),
                }
                for name, policy in self._policies.items()
            },
        }
    
    def enable_node(self, node_id: str) -> bool:
        """Enable a node."""
        if node_id in self._nodes:
            self._nodes[node_id].enabled = True
            return True
        return False
    
    def disable_node(self, node_id: str) -> bool:
        """Disable a node."""
        if node_id in self._nodes:
            self._nodes[node_id].enabled = False
            return True
        return False
    
    def clear_session_affinity(self, session_key: Optional[str] = None) -> int:
        """Clear session affinity."""
        if session_key:
            self._session_affinity.pop(session_key, None)
            return 1
        else:
            count = len(self._session_affinity)
            self._session_affinity.clear()
            return count


@dataclass
class RoutingDecision:
    """Result of a routing decision."""
    
    success: bool
    primary_node: Optional[PolicyNode] = None
    failover_chain: List[PolicyNode] = field(default_factory=list)
    policy_name: str = "default"
    strategy: RoutingStrategy = RoutingStrategy.WEIGHTED_RANDOM
    context: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    
    @property
    def selected_agent(self) -> Optional[str]:
        """Get primary agent ID."""
        if self.primary_node and self.primary_node.agent_ids:
            return self.primary_node.agent_ids[0]
        return None
    
    @property
    def failover_agents(self) -> List[str]:
        """Get failover agent IDs."""
        agents = []
        for node in self.failover_chain:
            agents.extend(node.agent_ids)
        return agents
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "primary_node": self.primary_node.node_id if self.primary_node else None,
            "primary_agent": self.selected_agent,
            "failover_nodes": [n.node_id for n in self.failover_chain],
            "failover_agents": self.failover_agents,
            "policy_name": self.policy_name,
            "strategy": self.strategy.value,
            "error": self.error,
            "timestamp": self.timestamp,
        }


# Backward compatibility
AgentNode = PolicyNode
AgentPolicy = RoutingPolicy