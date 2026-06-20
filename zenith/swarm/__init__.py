"""
swarm/__init__.py - Swarm Package
"""
from .agents import Swarm, PlannerAgent, TesterAgent, FixerAgent
from .reviewers import SwarmReview


def create_swarm():
    return Swarm()
