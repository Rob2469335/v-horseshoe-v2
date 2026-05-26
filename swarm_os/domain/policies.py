# swarm_os/domain/policies.py
from __future__ import annotations

from .models import SwarmJob, SwarmNode


def can_accept_job(node: SwarmNode, job: SwarmJob) -> bool:
    """Return True if node can accept this job."""
    if node.role == "disabled":
        return False
    if job.status != "pending":
        return False
    return True


def rejection_reason(node: SwarmNode, job: SwarmJob) -> str | None:
    """Return human-readable reason if job is rejected, None if accepted."""
    if node.role == "disabled":
        return f"Node {node.node_id!r} is disabled"
    if job.status != "pending":
        return f"Job {job.job_id!r} is not pending (status={job.status!r})"
    return None


def score_node(node: SwarmNode, job: SwarmJob) -> int:
    """Score a node for a given job. Higher = better fit."""
    score = 0
    if node.role == "worker":
        score += 10
    if node.role == "coordinator":
        score += 5
    if job.kind in node.state.get("skills", []):
        score += 20
    # Penalise busy nodes
    score -= node.state.get("active_jobs", 0) * 3
    return max(0, score)
