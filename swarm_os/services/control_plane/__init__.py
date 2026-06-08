# CONTROL PLANE IS LAZY-LOADED

def get_registry():
    from .registry import PluginRegistry
    return PluginRegistry

def get_router():
    from .router import Router
    return Router

