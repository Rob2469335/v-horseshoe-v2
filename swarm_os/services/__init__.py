"""
Service layer entrypoint.

IMPORTANT:
- Avoid heavy imports here
- Prevent circular initialization chains
"""

def get_orchestrator():
    from .orchestrator import Orchestrator
    return Orchestrator

