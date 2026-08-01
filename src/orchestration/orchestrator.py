"""
Multi-Agent Orchestrator with Self-Healing Capabilities.

Main orchestrator that integrates:
- Dynamic routing with policy graph
- Circuit breaker middleware
- Health monitoring
- Hybrid memory system
- Self-healing loop with retry and escalation
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

from .circuit_breaker import CircuitBreakerConfig, CircuitState
from .health_monitor import HealthMonitor, HealthThreshold
from .router import DynamicRouter, AgentRoute, RoutingRequest, RoutingResult, RoutingMode, create_dynamic_router
from .policy_graph import PolicyGraph, RoutingPolicy, RoutingStrategy, PolicyNode, NodeWeight

logger = logging.getLogger(__name__)

T = TypeVar('T')


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class EscalationLevel(Enum):
    """Escalation levels for self-healing."""
    LEVEL_0_RETRY = 0      # Retry with same parameters
    LEVEL_1_PARAMS = 1     # Retry with modified parameters
    LEVEL_2_FALLBACK = 2   # Escalate to fallback agent
    LEVEL_3_HUMAN = 3      # Escalate to human intervention
    LEVEL_4_ABORT = 4      # Abort task


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""
    
    max_retries: int = 3
    base_delay: float = 1.0          # Initial delay (seconds)
    max_delay: float = 60.0          # Max delay (seconds)
    exponential_base: float = 2.0    # Exponential backoff base
    jitter: float = 0.1              # Jitter factor (0-1)
    retry_on: tuple = (Exception,)   # Exception types to retry
    stop_on: tuple = ()              # Exception types to stop on


@dataclass
class EscalationPolicy:
    """Configuration for escalation behavior."""
    
    max_escalation_level: EscalationLevel = EscalationLevel.LEVEL_2_FALLBACK
    escalation_threshold: int = 3    # Failures before escalating
    escalation_delay: float = 5.0    # Delay before escalation
    fallback_agents: List[str] = field(default_factory=list)  # Fallback agent IDs
    human_escalation_webhook: Optional[str] = None


@dataclass
class OrchestrationConfig:
    """Main configuration for orchestrator."""
    
    # Routing
    routing_strategy: RoutingStrategy = RoutingStrategy.WEIGHTED_RANDOM
    default_timeout: float = 30.0
    max_failover_attempts: int = 3
    failover_timeout: float = 2.0
    
    # Retry
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    
    # Escalation
    escalation_policy: EscalationPolicy = field(default_factory=EscalationPolicy)
    
    # Circuit breaker
    circuit_breaker_config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    
    # Health monitoring
    health_thresholds: HealthThreshold = field(default_factory=HealthThreshold)
    health_evaluation_interval: float = 10.0
    
    # Self-healing
    enable_self_healing: bool = True
    healing_check_interval: float = 30.0
    
    # Memory
    enable_hybrid_memory: bool = True
    
    # General
    max_concurrent_tasks: int = 100
    task_history_size: int = 10000


@dataclass
class Task:
    """Task representation for orchestration."""
    
    task_id: str
    task_type: str
    payload: Any
    context: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    # Retry/escalation tracking
    retry_count: int = 0
    escalation_level: EscalationLevel = EscalationLevel.LEVEL_0_RETRY
    last_error: Optional[str] = None
    
    # Priority for queue ordering
    priority: int = 0
    
    # Routing
    assigned_agent: Optional[str] = None
    assigned_node: Optional[str] = None
    routing_policy: str = "default"
    
    # Results
    result: Any = None
    error: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Set[str] = field(default_factory=set)
    
    def duration_ms(self) -> Optional[float]:
        """Get task duration in milliseconds."""
        if self.started_at:
            end = self.completed_at or time.time()
            return (end - self.started_at) * 1000
        return None


class MultiAgentOrchestrator:
    """
    Multi-Agent Orchestrator with Self-Healing Capabilities.
    
    Coordinates multi-agent task execution with:
    - Dynamic routing with policy graph
    - Circuit breaker protection
    - Health-aware load balancing
    - Retry with exponential backoff
    - Escalation to fallback agents
    - Hybrid memory integration
    """
    
    def __init__(
        self,
        config: Optional[OrchestrationConfig] = None,
        router: Optional[DynamicRouter] = None,
        health_monitor: Optional[HealthMonitor] = None,
        policy_graph: Optional[PolicyGraph] = None,
        hybrid_memory: Optional[Any] = None,
    ):
        """
        Initialize orchestrator.
        
        Args:
            config: Orchestration configuration
            router: Pre-configured dynamic router
            health_monitor: Pre-configured health monitor
            policy_graph: Pre-configured policy graph
            hybrid_memory: Hybrid memory instance
        """
        self.config = config or OrchestrationConfig()
        self.hybrid_memory = hybrid_memory
        
        # Initialize components
        self.health_monitor = health_monitor or HealthMonitor(
            thresholds=self.config.health_thresholds,
            evaluation_interval=self.config.health_evaluation_interval,
        )
        
        self.policy_graph = policy_graph or PolicyGraph()
        self.policy_graph.set_health_monitor(self.health_monitor)
        
        self.router = router or DynamicRouter(
            policy_graph=self.policy_graph,
            health_monitor=self.health_monitor,
            circuit_breaker_config=self.config.circuit_breaker_config,
            default_timeout=self.config.default_timeout,
            max_failover_attempts=self.config.max_failover_attempts,
            failover_timeout=self.config.failover_timeout,
        )
        
        # Task management
        self._tasks: Dict[str, Task] = {}
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._active_tasks: Set[str] = set()
        self._task_semaphore = asyncio.Semaphore(self.config.max_concurrent_tasks)
        
        # Handler registry
        self._handlers: Dict[str, Callable[[AgentRoute, RoutingRequest], Awaitable[Any]]] = {}
        
        # Background tasks
        self._running = False
        self._worker_tasks: List[asyncio.Task] = []
        self._healing_task: Optional[asyncio.Task] = None
        
        # Metrics
        self._metrics = {
            "tasks_submitted": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "tasks_retried": 0,
            "tasks_escalated": 0,
            "total_latency_ms": 0.0,
        }
        
        logger.info("MultiAgentOrchestrator initialized")
    
    def register_handler(
        self,
        task_type: str,
        handler: Callable[[AgentRoute, RoutingRequest], Awaitable[Any]],
    ) -> None:
        """Register a handler for a task type."""
        self._handlers[task_type] = handler
        logger.info(f"Registered handler for task type: {task_type}")
    
    def register_agent(
        self,
        agent_id: str,
        node_id: str,
        endpoint: str,
        capabilities: Optional[Set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        max_concurrent: int = 10,
    ) -> None:
        """Register an agent with the router."""
        self.router.register_agent(
            agent_id=agent_id,
            node_id=node_id,
            endpoint=endpoint,
            capabilities=capabilities,
            metadata=metadata,
            max_concurrent=max_concurrent,
        )
    
    async def submit_task(
        self,
        task_type: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        priority: int = 0,
        timeout: Optional[float] = None,
        routing_policy: str = "default",
        tags: Optional[Set[str]] = None,
    ) -> str:
        """
        Submit a task for execution.
        
        Args:
            task_type: Type of task (must have registered handler)
            payload: Task payload
            context: Additional context for routing
            task_id: Optional custom task ID
            priority: Task priority (higher = more important)
            timeout: Task timeout in seconds
            routing_policy: Routing policy to use
            tags: Tags for categorization
            
        Returns:
            Task ID
        """
        if task_type not in self._handlers:
            raise ValueError(f"No handler registered for task type: {task_type}")
        
        task_id = task_id or str(uuid.uuid4())
        
        task = Task(
            task_id=task_id,
            task_type=task_type,
            payload=payload,
            context=context or {},
            priority=priority,
            routing_policy=routing_policy,
            tags=tags or set(),
        )
        
        # Store task
        self._tasks[task_id] = task
        self._metrics["tasks_submitted"] += 1
        
        # Add to queue with priority
        await self._task_queue.put((-priority, task_id))
        
        # Store in hybrid memory if enabled
        if self.hybrid_memory and self.config.enable_hybrid_memory:
            await self.hybrid_memory.remember(
                agent_id="orchestrator",
                content=f"Task submitted: {task_type} ({task_id})",
                episode_type="task_submitted",
                metadata={
                    "task_id": task_id,
                    "task_type": task_type,
                    "priority": priority,
                    "routing_policy": routing_policy,
                },
                importance=0.5,
                tags={"task", task_type},
            )
        
        logger.info(f"Task submitted: {task_id} ({task_type})")
        return task_id
    
    async def submit_and_wait(
        self,
        task_type: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        **kwargs,
    ) -> Any:
        """Submit task and wait for completion."""
        task_id = await self.submit_task(task_type, payload, context, timeout=timeout, **kwargs)
        return await self.wait_for_task(task_id, timeout)
    
    async def wait_for_task(
        self,
        task_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> Any:
        """Wait for task completion and return result."""
        start = time.time()
        timeout = timeout or self.config.default_timeout
        
        while True:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")
            
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                if task.status == TaskStatus.COMPLETED:
                    return task.result
                else:
                    raise Exception(task.error or "Task failed")
            
            if time.time() - start > timeout:
                raise TimeoutError(f"Task {task_id} timed out after {timeout}s")
            
            await asyncio.sleep(poll_interval)
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or running task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING):
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            logger.info(f"Task cancelled: {task_id}")
            return True
        
        return False
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get task status."""
        task = self._tasks.get(task_id)
        return task.status if task else None
    
    async def _worker_loop(self) -> None:
        """Main worker loop for task processing."""
        while self._running:
            try:
                # Get next task
                try:
                    _, task_id = await asyncio.wait_for(
                        self._task_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                task = self._tasks.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    continue
                
                # Acquire semaphore
                async with self._task_semaphore:
                    self._active_tasks.add(task_id)
                    await self._execute_task(task)
                    self._active_tasks.discard(task_id)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}")
    
    async def _execute_task(self, task: Task) -> None:
        """Execute a single task with retry/escalation logic."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        
        handler = self._handlers.get(task.task_type)
        if not handler:
            task.status = TaskStatus.FAILED
            task.error = f"No handler for task type: {task.task_type}"
            task.completed_at = time.time()
            self._metrics["tasks_failed"] += 1
            return
        
        retry_policy = self.config.retry_policy
        escalation_policy = self.config.escalation_policy
        
        while True:
            # Check cancellation
            if task.status == TaskStatus.CANCELLED:
                return
            
            # Build routing request
            request = RoutingRequest(
                request_id=task.task_id,
                task_type=task.task_type,
                payload=task.payload,
                context=task.context,
                policy_name=task.routing_policy,
                mode=RoutingMode.SYNCHRONOUS,
                timeout=task.metadata.get("timeout", self.config.default_timeout),
                priority=task.metadata.get("priority", 0),
            )
            
            try:
                # Execute with routing
                result = await self.router.route_request(request, handler)
                
                if result.success:
                    # Success!
                    task.status = TaskStatus.COMPLETED
                    task.result = result.result
                    task.assigned_agent = result.agent_id
                    task.assigned_node = result.node_id
                    task.completed_at = time.time()
                    
                    latency = task.duration_ms() or 0
                    self._metrics["tasks_completed"] += 1
                    self._metrics["total_latency_ms"] += latency
                    
                    # Record success in health monitor
                    if result.agent_id:
                        self.health_monitor.record_success(result.agent_id)
                        self.health_monitor.record_latency(result.agent_id, latency)
                    
                    # Store in memory
                    if self.hybrid_memory and self.config.enable_hybrid_memory:
                        await self.hybrid_memory.remember(
                            agent_id="orchestrator",
                            content=f"Task completed: {task.task_type} ({task.task_id})",
                            episode_type="task_completed",
                            metadata={
                                "task_id": task.task_id,
                                "task_type": task.task_type,
                                "agent_id": result.agent_id,
                                "latency_ms": latency,
                                "failover_used": result.failover_used,
                            },
                            importance=0.6,
                            tags={"task", task.task_type, "success"},
                        )
                    
                    logger.info(f"Task completed: {task.task_id} ({latency:.1f}ms)")
                    return
                
                else:
                    # Routing failed
                    raise Exception(result.error or "Routing failed")
                    
            except Exception as e:
                task.last_error = str(e)
                task.retry_count += 1
                
                # Record failure
                if task.assigned_agent:
                    self.health_monitor.record_error(task.assigned_agent, str(e))
                
                logger.warning(f"Task {task.task_id} attempt {task.retry_count} failed: {e}")
                
                # Check if we should stop retrying
                if isinstance(e, retry_policy.stop_on):
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    task.completed_at = time.time()
                    self._metrics["tasks_failed"] += 1
                    return
                
                # Check retry limit
                if task.retry_count >= retry_policy.max_retries:
                    # Escalate
                    await self._escalate_task(task, escalation_policy)
                    return
                
                # Retry with backoff
                task.status = TaskStatus.RETRYING
                self._metrics["tasks_retried"] += 1
                
                delay = min(
                    retry_policy.base_delay * (retry_policy.exponential_base ** (task.retry_count - 1)),
                    retry_policy.max_delay
                )
                # Add jitter
                import random
                delay *= (1 + random.uniform(-retry_policy.jitter, retry_policy.jitter))
                
                await asyncio.sleep(delay)
    
    async def _escalate_task(self, task: Task, policy: EscalationPolicy) -> None:
        """Escalate task to next level."""
        task.escalation_level = EscalationLevel(min(
            task.escalation_level.value + 1,
            policy.max_escalation_level.value
        ))
        
        logger.warning(f"Task {task.task_id} escalated to level {task.escalation_level.name}")
        self._metrics["tasks_escalated"] += 1
        
        if task.escalation_level == EscalationLevel.LEVEL_1_PARAMS:
            # Retry with modified parameters
            task.status = TaskStatus.RETRYING
            task.context["_escalated"] = True
            task.context["_escalation_level"] = task.escalation_level.value
            
            # Modify payload for retry (could be customized)
            if "retry_params" in task.context:
                task.payload = {**task.payload, **task.context["retry_params"]}
            
            await asyncio.sleep(policy.escalation_delay)
            await self._execute_task(task)
            
        elif task.escalation_level == EscalationLevel.LEVEL_2_FALLBACK:
            # Try fallback agents
            if policy.fallback_agents:
                task.status = TaskStatus.RETRYING
                task.routing_policy = "fallback"
                
                # Ensure fallback agents are registered as nodes with priority
                for i, agent_id in enumerate(policy.fallback_agents):
                    node_id = f"fallback_{agent_id}"
                    node = self.policy_graph.get_node(node_id)
                    if not node:
                        # Create fallback node
                        node = PolicyNode(
                            node_id=node_id,
                            agent_ids=[agent_id],
                            priority=i,
                        )
                        self.policy_graph.add_node(node)
                    else:
                        node.priority = i
                        if agent_id not in node.agent_ids:
                            node.agent_ids.append(agent_id)
                
                # Update policy to use fallback agents with priority failover
                fallback_policy = RoutingPolicy(
                    name="fallback",
                    strategy=RoutingStrategy.PRIORITY_FAILOVER,
                    allowed_nodes=set(f"fallback_{agent}" for agent in policy.fallback_agents),
                    enable_failover=True,
                    max_failover_attempts=len(policy.fallback_agents),
                )
                self.policy_graph.add_policy(fallback_policy)
                
                await asyncio.sleep(policy.escalation_delay)
                await self._execute_task(task)
            else:
                task.status = TaskStatus.FAILED
                task.error = f"Escalated to fallback but no fallback agents configured: {task.last_error}"
                task.completed_at = time.time()
                self._metrics["tasks_failed"] += 1
                
        elif task.escalation_level == EscalationLevel.LEVEL_3_HUMAN:
            # Escalate to human
            if policy.human_escalation_webhook:
                try:
                    # Would send webhook here
                    logger.critical(f"HUMAN ESCALATION: {task.task_id} - {task.last_error}")
                except Exception as e:
                    logger.error(f"Failed to send human escalation webhook: {e}")
            
            task.status = TaskStatus.ESCALATED
            task.error = f"Escalated to human: {task.last_error}"
            task.completed_at = time.time()
            self._metrics["tasks_failed"] += 1
            
        elif task.escalation_level == EscalationLevel.LEVEL_4_ABORT:
            # Abort task
            task.status = TaskStatus.FAILED
            task.error = f"Aborted after max escalation: {task.last_error}"
            task.completed_at = time.time()
            self._metrics["tasks_failed"] += 1
    
    async def _healing_loop(self) -> None:
        """Self-healing background loop."""
        while self._running:
            try:
                await asyncio.sleep(self.config.healing_check_interval)
                
                if not self.config.enable_self_healing:
                    continue
                
                # Check for stuck tasks
                await self._check_stuck_tasks()
                
                # Update circuit breakers based on health
                await self._update_circuit_breakers()
                
                # Consolidate memory
                if self.hybrid_memory and self.config.enable_hybrid_memory:
                    await self.hybrid_memory.consolidate_memories()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Healing loop error: {e}")
    
    async def _check_stuck_tasks(self) -> None:
        """Check for and recover stuck tasks."""
        now = time.time()
        stuck_threshold = 300.0  # 5 minutes
        
        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING and task.started_at:
                if now - task.started_at > stuck_threshold:
                    logger.warning(f"Stuck task detected: {task.task_id} (running for {now - task.started_at:.1f}s)")
                    # Could implement recovery logic here
    
    async def _update_circuit_breakers(self) -> None:
        """Update circuit breaker states based on health."""
        # Policy graph already updates weights from health
        await self.policy_graph.update_weights_from_health()
    
    async def start(self) -> None:
        """Start orchestrator background tasks."""
        if self._running:
            return
        
        self._running = True
        
        # Start components
        await self.router.start()
        
        # Start workers
        num_workers = min(4, self.config.max_concurrent_tasks)
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop())
            self._worker_tasks.append(task)
        
        # Start healing loop
        self._healing_task = asyncio.create_task(self._healing_loop())
        
        logger.info(f"Orchestrator started with {num_workers} workers")
    
    async def stop(self) -> None:
        """Stop orchestrator gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        # Cancel background tasks
        for task in self._worker_tasks:
            task.cancel()
        if self._healing_task:
            self._healing_task.cancel()
        
        # Wait for completion
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        if self._healing_task:
            await asyncio.gather(self._healing_task, return_exceptions=True)
        
        # Stop components
        await self.router.stop()
        
        # Persist memory
        if self.hybrid_memory and self.config.enable_hybrid_memory:
            await self.hybrid_memory.persist_all()
        
        logger.info("Orchestrator stopped")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get orchestrator metrics."""
        total_tasks = self._metrics["tasks_completed"] + self._metrics["tasks_failed"]
        avg_latency = (
            self._metrics["total_latency_ms"] / self._metrics["tasks_completed"]
            if self._metrics["tasks_completed"] > 0 else 0
        )
        
        return {
            **self._metrics,
            "active_tasks": len(self._active_tasks),
            "pending_tasks": self._task_queue.qsize(),
            "total_tasks": total_tasks,
            "success_rate": self._metrics["tasks_completed"] / total_tasks if total_tasks > 0 else 0,
            "retry_rate": self._metrics["tasks_retried"] / total_tasks if total_tasks > 0 else 0,
            "escalation_rate": self._metrics["tasks_escalated"] / total_tasks if total_tasks > 0 else 0,
            "avg_latency_ms": avg_latency,
            "registered_handlers": list(self._handlers.keys()),
            "registered_agents": len(self.router._routes),
            "router_metrics": self.router.get_metrics(),
        }
    
    def get_task_summary(self) -> Dict[str, int]:
        """Get task count by status."""
        summary = defaultdict(int)
        for task in self._tasks.values():
            summary[task.status.value] += 1
        return dict(summary)
    
    async def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check."""
        router_health = await self.router.health_check()
        
        return {
            "orchestrator": {
                "status": "healthy" if router_health["status"] == "healthy" else "degraded",
                "running": self._running,
                "active_tasks": len(self._active_tasks),
            },
            "router": router_health,
            "policy_graph": self.policy_graph.get_routing_stats(),
            "metrics": self.get_metrics(),
        }


# Convenience function
async def create_orchestrator(
    agents: List[Dict[str, Any]],
    handlers: Dict[str, Callable],
    config: Optional[OrchestrationConfig] = None,
    hybrid_memory: Optional[Any] = None,
) -> MultiAgentOrchestrator:
    """
    Create and start a fully configured orchestrator.
    
    Args:
        agents: List of agent configurations
        handlers: Dict of task_type -> handler function
        config: Optional configuration
        hybrid_memory: Optional hybrid memory instance
        
    Returns:
        Started MultiAgentOrchestrator
    """
    orchestrator = MultiAgentOrchestrator(config=config, hybrid_memory=hybrid_memory)
    
    # Register agents
    for agent in agents:
        orchestrator.register_agent(
            agent_id=agent["agent_id"],
            node_id=agent.get("node_id", agent["agent_id"]),
            endpoint=agent["endpoint"],
            capabilities=set(agent.get("capabilities", [])),
            metadata=agent.get("metadata", {}),
            max_concurrent=agent.get("max_concurrent", 10),
        )
    
    # Register handlers
    for task_type, handler in handlers.items():
        orchestrator.register_handler(task_type, handler)
    
    # Start
    await orchestrator.start()
    
    return orchestrator