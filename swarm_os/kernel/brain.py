import importlib

__all__ = ["BrainRegistry", "simple_brain", "registry"]


def __getattr__(name):
    if name in __all__:
        mod = importlib.import_module("swarm_os.brain")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
