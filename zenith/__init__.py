"""
OmniDev - Autonomous AI Software Engineer
"""
VERSION = "0.1.0"
__version__ = VERSION


def run(task: str):
    """Run a task"""
    from core.engine import Engine
    engine = Engine()
    return engine.run(task)
