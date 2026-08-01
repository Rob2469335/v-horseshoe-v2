"""
Health Monitor for Agent Self-Reporting and Monitoring.

Tracks agent health metrics including:
- Latency percentiles (p50, p95, p99)
- Error rates and types
- Throughput (requests/second)
- Resource utilization (CPU, memory)
- Custom health signals
- Automatic anomaly detection
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class HealthMetric(Enum):
    """Types of health metrics."""
    LATENCY_MS = "latency_ms"
    ERROR_RATE = "error_rate"
    THROUGHPUT_RPS = "throughput_rps"
    CPU_PERCENT = "cpu_percent"
    MEMORY_PERCENT = "memory_percent"
    QUEUE_DEPTH = "queue_depth"
    CIRCUIT_BREAKER_STATE = "circuit_breaker_state"
    CUSTOM = "custom"


class HealthStatus(Enum):
    """Overall health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthThreshold:
    """Thresholds for health evaluation."""
    
    # Latency thresholds (ms)
    latency_warning_ms: float = 1000.0
    latency_critical_ms: float = 5000.0
    
    # Error rate thresholds (0.0 - 1.0)
    error_rate_warning: float = 0.05
    error_rate_critical: float = 0.20
    
    # Throughput thresholds (requests/sec)
    throughput_warning_rps: float = 1.0
    throughput_critical_rps: float = 0.1
    
    # Resource thresholds (0.0 - 1.0)
    cpu_warning: float = 0.70
    cpu_critical: float = 0.90
    memory_warning: float = 0.75
    memory_critical: float = 0.95
    
    # Queue depth
    queue_warning: int = 50
    queue_critical: int = 200


@dataclass
class MetricSample:
    """Single metric sample with timestamp."""
    metric: HealthMetric
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class AgentHealth:
    """Comprehensive agent health report."""
    
    agent_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_update: float = field(default_factory=time.time)
    
    # Latency metrics (ms)
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    avg_latency_ms: float = 0.0
    
    # Error metrics
    error_rate: float = 0.0
    error_count: int = 0
    total_requests: int = 0
    errors_by_type: Dict[str, int] = field(default_factory=dict)
    
    # Throughput
    throughput_rps: float = 0.0
    requests_last_minute: int = 0
    
    # Resources
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    
    # Queue
    queue_depth: int = 0
    
    # Circuit breaker
    circuit_breaker_state: str = "closed"
    
    # Custom metrics
    custom_metrics: Dict[str, float] = field(default_factory=dict)
    
    # Issues detected
    warnings: List[str] = field(default_factory=list)
    critical_issues: List[str] = field(default_factory=list)
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "last_update": self.last_update,
            "latency": {
                "p50_ms": self.latency_p50,
                "p95_ms": self.latency_p95,
                "p99_ms": self.latency_p99,
                "avg_ms": self.avg_latency_ms,
            },
            "errors": {
                "rate": self.error_rate,
                "count": self.error_count,
                "total_requests": self.total_requests,
                "by_type": self.errors_by_type,
            },
            "throughput_rps": self.throughput_rps,
            "resources": {
                "cpu_percent": self.cpu_percent,
                "memory_percent": self.memory_percent,
            },
            "queue_depth": self.queue_depth,
            "circuit_breaker_state": self.circuit_breaker_state,
            "custom_metrics": self.custom_metrics,
            "warnings": self.warnings,
            "critical_issues": self.critical_issues,
            "metadata": self.metadata,
        }
    
    @property
    def is_healthy(self) -> bool:
        """Check if agent is healthy."""
        return self.status == HealthStatus.HEALTHY
    
    @property
    def is_degraded(self) -> bool:
        """Check if agent is degraded."""
        return self.status == HealthStatus.DEGRADED
    
    @property
    def is_unhealthy(self) -> bool:
        """Check if agent is unhealthy."""
        return self.status == HealthStatus.UNHEALTHY


class HealthMonitor:
    """
    Monitors agent health through self-reported metrics and automatic checks.
    
    Features:
    - Sliding window metrics calculation
    - Percentile latency tracking
    - Anomaly detection
    - Health status evaluation
    - Alert callbacks
    """
    
    def __init__(
        self,
        thresholds: Optional[HealthThreshold] = None,
        window_size: int = 1000,
        window_duration: float = 60.0,  # 1 minute
        evaluation_interval: float = 10.0,
    ):
        """
        Initialize health monitor.
        
        Args:
            thresholds: Health evaluation thresholds
            window_size: Maximum samples per metric
            window_duration: Time window for metrics (seconds)
            evaluation_interval: How often to evaluate health (seconds)
        """
        self.thresholds = thresholds or HealthThreshold()
        self.window_size = window_size
        self.window_duration = window_duration
        self.evaluation_interval = evaluation_interval
        
        # Per-agent metric storage
        self._metrics: Dict[str, Dict[HealthMetric, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=window_size))
        )
        
        # Computed health reports
        self._health_reports: Dict[str, AgentHealth] = {}
        
        # Alert callbacks
        self._alert_callbacks: List[Callable[[AgentHealth], None]] = []
        
        # Background evaluation
        self._evaluation_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        logger.info("Health monitor initialized")
    
    def record_metric(
        self,
        agent_id: str,
        metric: HealthMetric,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record a metric sample for an agent."""
        sample = MetricSample(
            metric=metric,
            value=value,
            timestamp=time.time(),
            labels=labels or {},
        )
        self._metrics[agent_id][metric].append(sample)
    
    def record_latency(self, agent_id: str, latency_ms: float) -> None:
        """Record request latency."""
        self.record_metric(agent_id, HealthMetric.LATENCY_MS, latency_ms)
    
    def record_error(self, agent_id: str, error_type: str) -> None:
        """Record an error occurrence."""
        self.record_metric(agent_id, HealthMetric.ERROR_RATE, 1.0, {"type": error_type})
    
    def record_success(self, agent_id: str) -> None:
        """Record a successful request."""
        self.record_metric(agent_id, HealthMetric.ERROR_RATE, 0.0)
    
    def record_throughput(self, agent_id: str, rps: float) -> None:
        """Record throughput."""
        self.record_metric(agent_id, HealthMetric.THROUGHPUT_RPS, rps)
    
    def record_resources(
        self,
        agent_id: str,
        cpu_percent: float,
        memory_percent: float,
    ) -> None:
        """Record resource utilization."""
        self.record_metric(agent_id, HealthMetric.CPU_PERCENT, cpu_percent)
        self.record_metric(agent_id, HealthMetric.MEMORY_PERCENT, memory_percent)
    
    def record_queue_depth(self, agent_id: str, depth: int) -> None:
        """Record queue depth."""
        self.record_metric(agent_id, HealthMetric.QUEUE_DEPTH, float(depth))
    
    def record_circuit_breaker_state(self, agent_id: str, state: str) -> None:
        """Record circuit breaker state."""
        state_map = {"closed": 0.0, "half_open": 0.5, "open": 1.0}
        self.record_metric(agent_id, HealthMetric.CIRCUIT_BREAKER_STATE, state_map.get(state, 0.0))
    
    def record_custom(self, agent_id: str, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Record custom metric."""
        self.record_metric(agent_id, HealthMetric.CUSTOM, value, {"name": name, **(labels or {})})
    
    def _prune_old_samples(self, agent_id: str) -> None:
        """Remove samples outside the time window."""
        cutoff = time.time() - self.window_duration
        for metric, samples in self._metrics[agent_id].items():
            while samples and samples[0].timestamp < cutoff:
                samples.popleft()
    
    def _calculate_latency_percentiles(self, samples: List[MetricSample]) -> tuple:
        """Calculate latency percentiles from samples."""
        if not samples:
            return 0.0, 0.0, 0.0, 0.0
        
        values = sorted(s.value for s in samples)
        n = len(values)
        
        p50 = values[n // 2]
        p95 = values[int(n * 0.95)]
        p99 = values[int(n * 0.99)]
        avg = statistics.mean(values)
        
        return p50, p95, p99, avg
    
    def _calculate_error_rate(self, samples: List[MetricSample]) -> tuple:
        """Calculate error rate and details from samples."""
        if not samples:
            return 0.0, 0, 0, {}
        
        total = len(samples)
        errors = sum(1 for s in samples if s.value > 0.5)
        error_rate = errors / total if total > 0 else 0.0
        
        # Count errors by type
        by_type = defaultdict(int)
        for s in samples:
            if s.value > 0.5:
                error_type = s.labels.get("type", "unknown")
                by_type[error_type] += 1
        
        return error_rate, errors, total, dict(by_type)
    
    def _calculate_throughput(self, samples: List[MetricSample]) -> tuple:
        """Calculate throughput from samples."""
        if not samples:
            return 0.0, 0
        
        # Use most recent throughput sample or calculate from request rate
        recent_rps = samples[-1].value if samples else 0.0
        
        # Count requests in window
        requests = sum(1 for s in samples if s.metric == HealthMetric.ERROR_RATE)
        
        return recent_rps, requests
    
    def _evaluate_health(self, agent_id: str) -> AgentHealth:
        """Evaluate health for a single agent."""
        self._prune_old_samples(agent_id)
        
        metrics = self._metrics[agent_id]
        health = AgentHealth(agent_id=agent_id, last_update=time.time())
        
        # Latency
        latency_samples = list(metrics.get(HealthMetric.LATENCY_MS, []))
        health.latency_p50, health.latency_p95, health.latency_p99, health.avg_latency_ms = \
            self._calculate_latency_percentiles(latency_samples)
        
        # Errors
        error_samples = list(metrics.get(HealthMetric.ERROR_RATE, []))
        health.error_rate, health.error_count, health.total_requests, health.errors_by_type = \
            self._calculate_error_rate(error_samples)
        
        # Throughput
        throughput_samples = list(metrics.get(HealthMetric.THROUGHPUT_RPS, []))
        health.throughput_rps, health.requests_last_minute = \
            self._calculate_throughput(throughput_samples)
        
        # Resources
        cpu_samples = list(metrics.get(HealthMetric.CPU_PERCENT, []))
        health.cpu_percent = cpu_samples[-1].value if cpu_samples else 0.0
        
        mem_samples = list(metrics.get(HealthMetric.MEMORY_PERCENT, []))
        health.memory_percent = mem_samples[-1].value if mem_samples else 0.0
        
        # Queue
        queue_samples = list(metrics.get(HealthMetric.QUEUE_DEPTH, []))
        health.queue_depth = int(queue_samples[-1].value) if queue_samples else 0
        
        # Circuit breaker
        cb_samples = list(metrics.get(HealthMetric.CIRCUIT_BREAKER_STATE, []))
        state_map = {0.0: "closed", 0.5: "half_open", 1.0: "open"}
        health.circuit_breaker_state = state_map.get(cb_samples[-1].value, "unknown") if cb_samples else "unknown"
        
        # Custom metrics
        custom_samples = list(metrics.get(HealthMetric.CUSTOM, []))
        for s in custom_samples:
            name = s.labels.get("name", "unknown")
            health.custom_metrics[name] = s.value
        
        # Evaluate status
        health.warnings = []
        health.critical_issues = []
        
        # Check latency
        if health.latency_p95 > self.thresholds.latency_critical_ms:
            health.critical_issues.append(f"Latency P95 critical: {health.latency_p95:.0f}ms")
            health.status = HealthStatus.UNHEALTHY
        elif health.latency_p95 > self.thresholds.latency_warning_ms:
            health.warnings.append(f"Latency P95 warning: {health.latency_p95:.0f}ms")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Check error rate
        if health.error_rate > self.thresholds.error_rate_critical:
            health.critical_issues.append(f"Error rate critical: {health.error_rate:.1%}")
            health.status = HealthStatus.UNHEALTHY
        elif health.error_rate > self.thresholds.error_rate_warning:
            health.warnings.append(f"Error rate warning: {health.error_rate:.1%}")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Check throughput
        if health.throughput_rps < self.thresholds.throughput_critical_rps and health.total_requests > 0:
            health.critical_issues.append(f"Throughput critical: {health.throughput_rps:.2f} rps")
            health.status = HealthStatus.UNHEALTHY
        elif health.throughput_rps < self.thresholds.throughput_warning_rps and health.total_requests > 0:
            health.warnings.append(f"Throughput warning: {health.throughput_rps:.2f} rps")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Check resources
        if health.cpu_percent > self.thresholds.cpu_critical:
            health.critical_issues.append(f"CPU critical: {health.cpu_percent:.1%}")
            health.status = HealthStatus.UNHEALTHY
        elif health.cpu_percent > self.thresholds.cpu_warning:
            health.warnings.append(f"CPU warning: {health.cpu_percent:.1%}")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        if health.memory_percent > self.thresholds.memory_critical:
            health.critical_issues.append(f"Memory critical: {health.memory_percent:.1%}")
            health.status = HealthStatus.UNHEALTHY
        elif health.memory_percent > self.thresholds.memory_warning:
            health.warnings.append(f"Memory warning: {health.memory_percent:.1%}")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Check queue
        if health.queue_depth > self.thresholds.queue_critical:
            health.critical_issues.append(f"Queue critical: {health.queue_depth}")
            health.status = HealthStatus.UNHEALTHY
        elif health.queue_depth > self.thresholds.queue_warning:
            health.warnings.append(f"Queue warning: {health.queue_depth}")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Check circuit breaker
        if health.circuit_breaker_state == "open":
            health.critical_issues.append("Circuit breaker OPEN")
            health.status = HealthStatus.UNHEALTHY
        elif health.circuit_breaker_state == "half_open":
            health.warnings.append("Circuit breaker HALF_OPEN")
            if health.status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN):
                health.status = HealthStatus.DEGRADED
        
        # Default to healthy if no issues
        if health.status == HealthStatus.UNKNOWN:
            health.status = HealthStatus.HEALTHY
        
        return health
    
    async def evaluate_all(self) -> Dict[str, AgentHealth]:
        """Evaluate health for all agents."""
        async with self._lock:
            reports = {}
            for agent_id in self._metrics.keys():
                reports[agent_id] = self._evaluate_health(agent_id)
                self._health_reports[agent_id] = reports[agent_id]
                
                # Trigger alerts for critical issues
                if reports[agent_id].critical_issues:
                    for callback in self._alert_callbacks:
                        try:
                            callback(reports[agent_id])
                        except Exception as e:
                            logger.error(f"Alert callback error: {e}")
            
            return reports
    
    def get_health(self, agent_id: str) -> Optional[AgentHealth]:
        """Get latest health report for agent."""
        return self._health_reports.get(agent_id)
    
    def get_all_health(self) -> Dict[str, AgentHealth]:
        """Get all health reports."""
        return dict(self._health_reports)
    
    def get_healthy_agents(self) -> List[str]:
        """Get list of healthy agent IDs."""
        return [
            agent_id for agent_id, health in self._health_reports.items()
            if health.is_healthy
        ]
    
    def get_degraded_agents(self) -> List[str]:
        """Get list of degraded agent IDs."""
        return [
            agent_id for agent_id, health in self._health_reports.items()
            if health.is_degraded
        ]
    
    def get_unhealthy_agents(self) -> List[str]:
        """Get list of unhealthy agent IDs."""
        return [
            agent_id for agent_id, health in self._health_reports.items()
            if health.is_unhealthy
        ]
    
    def add_alert_callback(self, callback: Callable[[AgentHealth], None]) -> None:
        """Add callback for health alerts."""
        self._alert_callbacks.append(callback)
    
    def remove_alert_callback(self, callback: Callable[[AgentHealth], None]) -> bool:
        """Remove alert callback."""
        try:
            self._alert_callbacks.remove(callback)
            return True
        except ValueError:
            return False
    
    async def start_evaluation(self) -> None:
        """Start background health evaluation."""
        if self._running:
            return
        
        self._running = True
        self._evaluation_task = asyncio.create_task(self._evaluation_loop())
        logger.info("Health monitor evaluation started")
    
    async def stop_evaluation(self) -> None:
        """Stop background health evaluation."""
        self._running = False
        if self._evaluation_task:
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor evaluation stopped")
    
    async def _evaluation_loop(self) -> None:
        """Background evaluation loop."""
        while self._running:
            try:
                await asyncio.sleep(self.evaluation_interval)
                await self.evaluate_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health evaluation error: {e}")
    
    def clear_agent(self, agent_id: str) -> None:
        """Clear all metrics for an agent."""
        if agent_id in self._metrics:
            del self._metrics[agent_id]
        if agent_id in self._health_reports:
            del self._health_reports[agent_id]
    
    def clear_all(self) -> None:
        """Clear all metrics."""
        self._metrics.clear()
        self._health_reports.clear()