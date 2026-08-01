"""
Enhanced Agent Runtime with Self-Healing Capabilities.

Extends the base AgentRuntime with:
- Task failure detection and automatic retry
- Exponential backoff with jitter
- Parameter modification on retry
- Escalation to fallback chain after N consecutive failures
- Integration with circuit breaker and health monitoring
- Hybrid memory for context retention

Based on Semantic Kernel's resilience patterns and 2025-2026 agentic recovery research.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from swarm_os.tool_runtime import CapabilityToolExecutor
from swarm_os.exceptions import ApprovalRequiredError

# Import orchestration components
from src.orchestration.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState, CircuitOpenError
from src.orchestration.health_monitor import HealthMonitor, HealthThreshold
from src.orchestration.router import DynamicRouter, AgentRoute, RoutingRequest, RoutingResult, RoutingMode
from src.orchestration.policy_graph import PolicyGraph, RoutingPolicy, RoutingStrategy
from src.orchestration.orchestrator import TaskStatus, EscalationLevel

ORCHESTRATION_AVAILABLE = True

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""
    
    max_retries: int = 3
    base_delay: float = 1.0           # Initial delay (seconds)
    max_delay: float = 60.0           # Maximum delay (seconds)
    exponential_base: float = 2.0     # Exponential backoff base
    jitter: float = 0.1               # Jitter factor (0-1)
    retry_on: Tuple[type, ...] = (Exception,)  # Exception types to retry
    stop_on: Tuple[type, ...] = ()    # Exception types to not retry


@dataclass
class EscalationPolicy:
    """Configuration for escalation behavior."""
    
    max_escalation_level: EscalationLevel = EscalationLevel.LEVEL_2_FALLBACK
    escalation_threshold: int = 3     # Failures before escalating
    escalation_delay: float = 5.0     # Delay before escalation (seconds)
    fallback_agents: List[str] = field(default_factory=list)  # Fallback agent IDs
    human_webhook: Optional[str] = None
    abort_on_max_escalation: bool = True


@dataclass
class SelfHealingConfig:
    """Complete self-healing configuration."""
    
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    escalation_policy: EscalationPolicy = field(default_factory=EscalationPolicy)
    
    # Circuit breaker integration
    enable_circuit_breaker: bool = True
    circuit_breaker_config: Optional[CircuitBreakerConfig] = None
    
    # Health monitoring
    enable_health_monitoring: bool = True
    health_thresholds: Optional[HealthThreshold] = None
    
    # Memory integration
    enable_hybrid_memory: bool = True
    memory_config: Optional[Any] = None
    
    # Task management
    max_concurrent_tasks: int = 100
    task_timeout: float = 300.0       # 5 minutes default
    task_history_size: int = 10000


@dataclass
class Task:
    """Task representation for self-healing runtime."""
    
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
    last_attempt_at: Optional[float] = None
    
    # Routing
    assigned_agent: Optional[str] = None
    assigned_node: Optional[str] = None
    routing_policy: str = "default"
    
    # Results
    result: Any = None
    error: Optional[str] = None
    
    # Original payload for retry comparison
    original_payload: Any = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Set[str] = field(default_factory=set)
    
    def duration_ms(self) -> Optional[float]:
        """Get task duration in milliseconds."""
        if self.started_at:
            end = self.completed_at or time.time()
            return (end - self.started_at) * 1000
        return None


class SelfHealingAgentRuntime:
    """
    Enhanced Agent Runtime with Self-Healing Capabilities.
    
    Features:
    - Automatic task retry with exponential backoff
    - Parameter modification on retry
    - Escalation to fallback chain after N consecutive failures
    - Circuit breaker integration
    - Health-aware routing
    - Hybrid memory for context retention
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        self_healing_config: Optional[SelfHealingConfig] = None,
    ):
        """
        Initialize self-healing agent runtime.
        
        Args:
            config: Base configuration for tool executor
            self_healing_config: Self-healing specific configuration
        """
        self.config = config or {}
        self.healing_config = self_healing_config or SelfHealingConfig()
        
        # Base tool executor
        self.tool_executor = CapabilityToolExecutor(config=self.config)
        self._active_tools: Set[str] = set(self.tool_executor.get_capabilities())
        self.approved_actions: List[Dict] = []
        
        # Self-healing components
        self._tasks: Dict[str, Task] = {}
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._active_tasks: Set[str] = set()
        self._task_semaphore = asyncio.Semaphore(self.healing_config.max_concurrent_tasks)
        self._task_history: List[Task] = []
        self._max_history = self.healing_config.task_history_size
        
        # Handlers
        self._handlers: Dict[str, Callable[[Any], Awaitable[Any]]] = {}
        self._param_modifiers: Dict[str, Callable[[Any, int], Any]] = {}
        
        # Orchestration components (if available)
        self._orchestration_enabled = ORCHESTRATION_AVAILABLE
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._health_monitor: Optional[HealthMonitor] = None
        self._router: Optional[DynamicRouter] = None
        self._policy_graph: Optional[PolicyGraph] = None
        self._hybrid_memory: Optional[Any] = None
        
        # Background tasks
        self._healing_task: Optional[asyncio.Task] = None
        self._running = False

        # Fire-and-forget task tracking (strong refs prevent GC mid-await)
        self._bg_tasks: Set[asyncio.Task] = set()
        
        # Metrics
        self._metrics = defaultdict(int)
        self._metrics_lock = asyncio.Lock()
        
        logger.info("SelfHealingAgentRuntime initialized with orchestration=%s", self._orchestration_enabled)
    
    async def initialize(self) -> None:
        """Initialize orchestration components."""
        if not self._orchestration_enabled:
            logger.warning("Orchestration components not available, running in basic mode")
            return
        
        # Initialize health monitor
        if self.healing_config.enable_health_monitoring:
            self._health_monitor = HealthMonitor(
                thresholds=self.healing_config.health_thresholds or HealthThreshold(),
            )
            await self._health_monitor.start_evaluation()
        
        # Initialize policy graph
        self._policy_graph = PolicyGraph()
        if self._health_monitor:
            self._policy_graph.set_health_monitor(self._health_monitor)
        
        # Initialize router
        self._router = DynamicRouter(
            policy_graph=self._policy_graph,
            health_monitor=self._health_monitor,
            circuit_breaker_config=self.healing_config.circuit_breaker_config or CircuitBreakerConfig(),
            default_timeout=self.healing_config.task_timeout,
            max_failover_attempts=self.healing_config.escalation_policy.escalation_threshold,
            failover_timeout=self.healing_config.escalation_policy.escalation_delay,
        )
        
        # Initialize hybrid memory
        if self.healing_config.enable_hybrid_memory:
            from src.agent_memory.hybrid_memory import HybridMemory, MemoryConfig
            self._hybrid_memory = HybridMemory(
                config=self.healing_config.memory_config or MemoryConfig(),
            )
            await self._hybrid_memory.initialize()
        
        logger.info("Orchestration components initialized")
    
    async def start(self) -> None:
        """Start background healing loop."""
        if self._running:
            return
        
        self._running = True
        self._healing_task = asyncio.create_task(self._healing_loop())
        
        if self._router:
            await self._router.start()
        
        logger.info("Self-healing runtime started")
    
    async def stop(self) -> None:
        """Stop background tasks."""
        self._running = False
        
        if self._healing_task:
            self._healing_task.cancel()
            try:
                await self._healing_task
            except asyncio.CancelledError:
                pass
        
        if self._router:
            await self._router.stop()
        
        if self._health_monitor:
            await self._health_monitor.stop_evaluation()
        
        # Persist memory
        if self._hybrid_memory:
            await self._hybrid_memory.persist_all()
        
        logger.info("Self-healing runtime stopped")
    
    # --- Tool Management (from base runtime) ---
    
    def list_tools(self) -> List[str]:
        return list(self._active_tools)
    
    def enable_tool(self, capability_name: str) -> None:
        capability_name = capability_name.lower().strip()
        available = self.tool_executor.get_capabilities()
        if capability_name not in available:
            raise KeyError(f"Tool '{capability_name}' not available. Available: {available}")
        self._active_tools.add(capability_name)
        logger.info("Enabled tool '%s'", capability_name)
    
    def disable_tool(self, capability_name: str) -> None:
        capability_name = capability_name.lower().strip()
        if capability_name in self._active_tools:
            self._active_tools.remove(capability_name)
            logger.info("Disabled tool '%s'", capability_name)
    
    def is_state_changing(self, tool_name: str, payload: Any) -> bool:
        tool_name = tool_name.lower().strip()
        if not isinstance(payload, dict):
            return False
        if tool_name == "filesystem":
            op = payload.get("operation", "").lower().strip()
            return op in ("write", "patch", "delete")
        if tool_name == "sandbox_repl":
            lang = payload.get("language", "").lower().strip()
            return lang in ("python", "powershell")
        return False
    
    async def call_tool(
        self,
        capability_name: str,
        payload: Any,
        cache_key: Optional[str] = None,
    ) -> Any:
        """Call a tool with circuit breaker protection."""
        capability_name = capability_name.lower().strip()
        
        # Approval check
        if self.is_state_changing(capability_name, payload):
            approved = False
            for action in self.approved_actions:
                if action.get("tool") == capability_name and action.get("payload") == payload:
                    approved = True
                    self.approved_actions.remove(action)
                    break
            if not approved:
                from swarm_os.exceptions import ApprovalRequiredError
                raise ApprovalRequiredError(capability_name, payload)
        
        # Filesystem write redirect
        if capability_name == "filesystem" and isinstance(payload, dict) and payload.get("operation") == "write":
            from swarm_os.tools.file_tools import write_file
            path = payload.get("path")
            content = payload.get("content")
            if not path or not content:
                return {"error": "Missing path or content for write operation"}
            result_msg = write_file(path, content)
            return {"status": "success" if "Success" in result_msg else "error", "message": result_msg}
        
        if capability_name not in self._active_tools:
            if capability_name in self.tool_executor.get_capabilities():
                self._active_tools.add(capability_name)
            else:
                raise RuntimeError(
                    f"Tool '{capability_name}' is disabled. "
                    f"Active tools: {list(self._active_tools)}"
                )
        
        # Circuit breaker protection
        if self._orchestration_enabled and self.healing_config.enable_circuit_breaker:
            breaker = self._get_circuit_breaker(capability_name)
            try:
                return await breaker.call(
                    self.tool_executor.execute_tool,
                    capability_name,
                    payload,
                    cache_key=cache_key,
                )
            except CircuitOpenError:
                # Try fallback agent if available
                if self._router:
                    return await self._execute_via_fallback(capability_name, payload)
                raise
        
        return await self.tool_executor.execute_tool(capability_name, payload, cache_key=cache_key)
    
    def _get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """Get or create circuit breaker for a tool/agent."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(
                name=f"tool_{name}",
                config=self.healing_config.circuit_breaker_config or CircuitBreakerConfig(),
            )
        return self._circuit_breakers[name]
    
    async def _execute_via_fallback(
        self,
        capability_name: str,
        payload: Any,
    ) -> Any:
        """Execute via fallback agent through router."""
        if not self._router:
            raise RuntimeError("No router available for fallback")
        
        # Find agent with this capability
        for agent_id, route in self._router._routes.items():
            if capability_name in route.capabilities:
                request = RoutingRequest(
                    request_id=str(uuid.uuid4()),
                    task_type=capability_name,
                    payload=payload,
                )
                result = await self._router.route_request(request, self._default_handler)
                if result.success:
                    return result.result
        
        raise RuntimeError(f"No fallback agent found for capability: {capability_name}")
    
    async def _default_handler(self, route: AgentRoute, request: RoutingRequest) -> Any:
        """Default handler for routed requests."""
        return {"status": "executed", "agent": route.agent_id, "task": request.task_type}
    
    # --- Task Execution with Self-Healing ---
    
    def register_handler(
        self,
        task_type: str,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> None:
        """Register a handler for a task type."""
        self._handlers[task_type] = handler
        logger.info("Registered handler for task type: %s", task_type)
    
    def register_param_modifier(
        self,
        task_type: str,
        modifier: Callable[[Any, int], Any],
    ) -> None:
        """Register a parameter modifier for retries.
        
        Args:
            task_type: Task type to modify
            modifier: Function(original_payload, retry_count) -> modified_payload
        """
        self._param_modifiers[task_type] = modifier
        logger.info("Registered param modifier for task type: %s", task_type)
    
    async def execute_task(
        self,
        task_type: str,
        payload: Any,
        context: Optional[Dict[str, Any]] = None,
        routing_policy: str = "default",
        timeout: Optional[float] = None,
    ) -> Task:
        """
        Execute a task with self-healing.
        
        Args:
            task_type: Type of task to execute
            payload: Task payload
            context: Optional execution context
            routing_policy: Routing policy to use
            timeout: Task timeout in seconds
            
        Returns:
            Task object with result
        """
        if task_type not in self._handlers:
            raise ValueError(f"No handler registered for task type: {task_type}")
        
        task = Task(
            task_id=str(uuid.uuid4()),
            task_type=task_type,
            payload=payload,
            context=context or {},
            original_payload=payload,
            routing_policy=routing_policy,
        )
        
        self._tasks[task.task_id] = task
        self._metrics["tasks_submitted"] += 1
        
        # Store in hybrid memory if enabled
        if self._hybrid_memory and self.healing_config.enable_hybrid_memory:
            await self._hybrid_memory.remember(
                agent_id="runtime",
                content=f"Task {task.task_id}: {task_type}",
                episode_type="task_start",
                metadata={
                    "task_id": task.task_id,
                    "task_type": task_type,
                    "routing_policy": routing_policy,
                },
                importance=0.5,
                tags={"task", task_type},
            )
        
        # Execute with self-healing
        await self._execute_with_healing(task, timeout or self.healing_config.task_timeout)
        
        return task
    
    async def _execute_with_healing(self, task: Task, timeout: float) -> None:
        """Execute task with retry and escalation logic."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        
        await self._increment_metric("tasks_started")
        
        try:
            handler = self._handlers.get(task.task_type)
            if not handler:
                raise ValueError(f"No handler for task type: {task.task_type}")
            
            # Store in hybrid memory
            if self._hybrid_memory and self.healing_config.enable_hybrid_memory:
                await self._hybrid_memory.remember(
                    agent_id="runtime",
                    content=f"Task {task.task_id}: {task.task_type}",
                    episode_type="task_start",
                    metadata={
                        "task_id": task.task_id,
                        "task_type": task.task_type,
                        **task.context,
                    },
                )
            
            # Execute with retry loop
            last_exception = None
            
            for attempt in range(self.healing_config.retry_policy.max_retries + 1):
                task.retry_count = attempt
                task.last_attempt_at = time.time()
                
                if attempt > 0:
                    task.status = TaskStatus.RETRYING
                    await self._increment_metric("task_retries")
                    logger.info("Retrying task %s (attempt %d)", task.task_id, attempt)
                    
                    # Apply parameter modification on retries
                    if attempt >= 1 and task.task_type in self._param_modifiers:
                        modifier = self._param_modifiers[task.task_type]
                        task.payload = modifier(task.original_payload, attempt)
                        logger.debug("Modified payload for task %s on retry %d", task.task_id, attempt)
                    
                    # Exponential backoff
                    delay = self._calculate_backoff(attempt)
                    await asyncio.sleep(delay)
                
                try:
                    # Execute with timeout
                    task.result = await asyncio.wait_for(
                        handler(task.payload),
                        timeout=timeout,
                    )
                    
                    # Success!
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    task.assigned_agent = "local"  # Would be actual agent in distributed mode
                    
                    await self._increment_metric("tasks_completed")
                    await self._increment_metric("total_latency_ms", task.duration_ms() or 0)
                    
                    # Record success in health monitor
                    if self._health_monitor and task.assigned_agent:
                        self._health_monitor.record_success(task.assigned_agent)
                        self._health_monitor.record_latency(task.assigned_agent, task.duration_ms() or 0)
                    
                    # Store result in memory
                    if self._hybrid_memory and self.healing_config.enable_hybrid_memory:
                        await self._hybrid_memory.remember(
                            agent_id=task.assigned_agent or "runtime",
                            content=f"Task {task.task_id} completed successfully",
                            episode_type="task_complete",
                            metadata={
                                "task_id": task.task_id,
                                "result": str(task.result)[:200],
                                "latency_ms": task.duration_ms(),
                            },
                            importance=0.6,
                            tags={"task", task.task_type, "success"},
                        )
                    
                    logger.info("Task %s completed successfully (%.1fms)", task.task_id, task.duration_ms() or 0)
                    return
                    
                except self.healing_config.retry_policy.stop_on as e:
                    # Don't retry on stop exceptions
                    last_exception = e
                    logger.warning("Task %s stopped by exception: %s", task.task_id, e)
                    break
                    
                except self.healing_config.retry_policy.retry_on as e:
                    last_exception = e
                    task.last_error = str(e)
                    
                    # Record failure in health monitor
                    if self._health_monitor and task.assigned_agent:
                        self._health_monitor.record_failure(task.assigned_agent, str(e))
                    
                    logger.warning("Task %s attempt %d failed: %s", task.task_id, attempt + 1, e)
                    continue
            
            # All retries exhausted - escalate
            await self._escalate_task(task, last_exception)
            
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            raise
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            await self._increment_metric("tasks_failed")
            logger.error("Task %s failed with unexpected error: %s", task.task_id, e)
            
            if self._hybrid_memory and self.healing_config.enable_hybrid_memory:
                await self._hybrid_memory.remember(
                    agent_id="runtime",
                    content=f"Task {task.task_id} failed: {e}",
                    episode_type="task_failed",
                    metadata={"task_id": task.task_id, "error": str(e)},
                )
        
        finally:
            await self._cleanup_task(task)
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        policy = self.healing_config.retry_policy
        delay = min(
            policy.base_delay * (policy.exponential_base ** attempt),
            policy.max_delay,
        )
        # Add jitter
        jitter = delay * policy.jitter * (2 * asyncio.get_running_loop().time() % 1 - 1)
        return delay + jitter
    
    async def _escalate_task(self, task: Task, last_exception: Optional[Exception]) -> None:
        """Escalate task through escalation levels."""
        task.escalation_level = EscalationLevel(min(
            task.escalation_level.value + 1,
            self.healing_config.escalation_policy.max_escalation_level.value
        ))
        
        logger.warning("Task %s escalated to level %s", task.task_id, task.escalation_level.name)
        await self._increment_metric("tasks_escalated")
        
        if task.escalation_level == EscalationLevel.LEVEL_1_PARAMS:
            # Retry with modified parameters
            task.status = TaskStatus.RETRYING
            task.context["_escalated"] = True
            task.context["_escalation_level"] = task.escalation_level.value
            
            if task.task_type in self._param_modifiers:
                modifier = self._param_modifiers[task.task_type]
                task.payload = modifier(task.payload, task.retry_count + 1)
            
            await asyncio.sleep(self.healing_config.escalation_policy.escalation_delay)
            await self._execute_with_healing(task, self.healing_config.task_timeout)
            
        elif task.escalation_level == EscalationLevel.LEVEL_2_FALLBACK:
            # Try fallback agents
            if self.healing_config.escalation_policy.fallback_agents:
                task.status = TaskStatus.RETRYING
                task.routing_policy = "fallback"
                
                # Ensure fallback agents are registered as nodes
                for i, agent_id in enumerate(self.healing_config.escalation_policy.fallback_agents):
                    node_id = f"fallback_{agent_id}"
                    node = self._policy_graph.get_node(node_id) if self._policy_graph else None
                    if not node:
                        node = PolicyNode(
                            node_id=node_id,
                            agent_ids=[agent_id],
                            priority=i,
                        )
                        self._policy_graph.add_node(node)
                    else:
                        node.priority = i
                        if agent_id not in node.agent_ids:
                            node.agent_ids.append(agent_id)
                
                # Update policy to use fallback agents
                if self._policy_graph:
                    fallback_policy = RoutingPolicy(
                        name="fallback",
                        strategy=RoutingStrategy.PRIORITY_FAILOVER,
                        allowed_nodes=set(f"fallback_{agent}" for agent in self.healing_config.escalation_policy.fallback_agents),
                        enable_failover=True,
                        max_failover_attempts=len(self.healing_config.escalation_policy.fallback_agents),
                    )
                    self._policy_graph.add_policy(fallback_policy)
                
                await asyncio.sleep(self.healing_config.escalation_policy.escalation_delay)
                await self._execute_with_healing(task, self.healing_config.task_timeout)
            else:
                task.status = TaskStatus.FAILED
                task.error = f"Escalated to fallback but no fallback agents configured: {last_exception}"
                task.completed_at = time.time()
                await self._increment_metric("tasks_failed")
                
        elif task.escalation_level == EscalationLevel.LEVEL_3_HUMAN:
            # Escalate to human
            if self.healing_config.escalation_policy.human_webhook:
                try:
                    # Would send webhook here
                    logger.critical("HUMAN ESCALATION: Task %s requires intervention. Error: %s", task.task_id, last_exception)
                except Exception as e:
                    logger.error("Failed to send human escalation webhook: %s", e)
            
            task.status = TaskStatus.ESCALATED
            task.error = f"Escalated to human: {last_exception}"
            task.completed_at = time.time()
            await self._increment_metric("tasks_failed")
            
        elif task.escalation_level == EscalationLevel.LEVEL_4_ABORT:
            # Abort task
            task.status = TaskStatus.FAILED
            task.error = f"Aborted after max escalation: {last_exception}"
            task.completed_at = time.time()
            await self._increment_metric("tasks_failed")
    
    async def _cleanup_task(self, task: Task) -> None:
        """Clean up task and add to history."""
        self._active_tasks.discard(task.task_id)
        
        # Add to history
        self._task_history.append(task)
        if len(self._task_history) > self._max_history:
            self._task_history = self._task_history[-self._max_history:]
        
        # Remove from active tasks after delay (keep for inspection)
        task_ref = asyncio.create_task(self._delayed_task_removal(task.task_id))
        self._bg_tasks.add(task_ref)
        task_ref.add_done_callback(self._bg_tasks.discard)
    
    async def _delayed_task_removal(self, task_id: str, delay: float = 300.0) -> None:
        """Remove task from active dict after delay."""
        await asyncio.sleep(delay)
        self._tasks.pop(task_id, None)
    
    # --- Background Healing Loop ---
    
    async def _healing_loop(self) -> None:
        """Background loop for self-healing maintenance."""
        while self._running:
            try:
                await asyncio.sleep(self.healing_config.task_timeout)
                
                if not self.healing_config.enable_self_healing:
                    continue
                
                # Update circuit breaker weights from health
                if self._policy_graph:
                    await self._policy_graph.update_weights_from_health()
                
                # Clean up stuck tasks
                await self._cleanup_stuck_tasks()
                
                # Consolidate memories
                if self._hybrid_memory and self.healing_config.enable_hybrid_memory:
                    await self._hybrid_memory.consolidate_memories()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Healing loop error: %s", e)
    
    async def _cleanup_stuck_tasks(self) -> None:
        """Find and handle stuck tasks."""
        now = time.time()
        stuck_threshold = self.healing_config.task_timeout * 2
        
        for task in list(self._tasks.values()):
            if task.status == TaskStatus.RUNNING and task.started_at:
                if now - task.started_at > stuck_threshold:
                    logger.warning("Stuck task detected: %s (running for %.0fs)", 
                                 task.task_id, now - task.started_at)
                    task.status = TaskStatus.FAILED
                    task.error = "Task timeout - marked as stuck"
                    task.completed_at = now
                    await self._increment_metric("tasks_stuck")
    
    # --- Agent Registration (for router) ---
    
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
        if self._router:
            self._router.register_agent(
                agent_id=agent_id,
                node_id=node_id,
                endpoint=endpoint,
                capabilities=capabilities,
                metadata=metadata,
                max_concurrent=max_concurrent,
            )
            
            # Also register with health monitor
            if self._health_monitor:
                self._health_monitor.record_latency(agent_id, 0.0)
    
    # --- Metrics ---
    
    async def _increment_metric(self, name: str, value: int = 1) -> None:
        """Increment a metric counter."""
        async with self._metrics_lock:
            self._metrics[name] += value
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get runtime metrics."""
        return {
            "tasks": dict(self._metrics),
            "active_tasks": len(self._active_tasks),
            "queued_tasks": self._task_queue.qsize(),
            "registered_handlers": list(self._handlers.keys()),
            "registered_agents": len(self._router._routes) if self._router else 0,
            "circuit_breakers": {
                name: cb.get_status() 
                for name, cb in self._circuit_breakers.items()
            } if self._orchestration_enabled else {},
            "health": self._health_monitor.get_all_health() if self._health_monitor else {},
            "memory": self._hybrid_memory.get_stats() if self._hybrid_memory else {},
        }
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_recent_tasks(self, limit: int = 100) -> List[Task]:
        """Get recent tasks."""
        return self._task_history[-limit:]
    
    # --- Health Check ---
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check."""
        checks = {
            "runtime": "healthy",
            "tools": len(self._active_tools),
            "handlers": len(self._handlers),
            "active_tasks": len(self._active_tasks),
        }
        
        if self._orchestration_enabled:
            if self._router:
                router_health = await self._router.health_check()
                checks["router"] = router_health
            
            if self._health_monitor:
                checks["health_monitor"] = "healthy"
            
            if self._hybrid_memory:
                checks["memory"] = "healthy"
        
        # Determine overall status
        unhealthy = any(v != "healthy" for v in checks.values() if isinstance(v, str))
        checks["overall"] = "degraded" if unhealthy else "healthy"
        
        return checks
    
    # --- Base Runtime Compatibility ---
    
    def get_tool_cache_size(self) -> int:
        return self.tool_executor.cache_size()
    
    def clear_tool_cache(self) -> None:
        self.tool_executor.clear_cache()
        logger.info("Cleared agent runtime tool cache")


# Backward compatibility alias
AgentRuntime = SelfHealingAgentRuntime