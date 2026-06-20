from __future__ import annotations

from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.shared_model_registry import (
    CLOUD_MODEL_SPECS,
    LOCAL_MODEL_SPECS,
    ROLE_POOL,
    ModelProfile,
)

_ROUTER: Router | None = None

def bootstrap_control_plane() -> None:
    """
    Initialize the control plane. This is called during app lifespan startup.
    It ensures strategies are registered and the global router is ready.
    """
    global _ROUTER
    # Strategy registration happens at the module level in strategy_registry.py
    if _ROUTER is None:
        _ROUTER = build_router(include_cloud=True)

def get_router(include_cloud: bool = True) -> Router:
    """
    Get or build the global router instance.
    """
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = build_router(include_cloud=include_cloud)
    return _ROUTER

def build_profiles(include_cloud: bool = True) -> list[ModelProfile]:
    profiles = list(LOCAL_MODEL_SPECS)
    if include_cloud:
        profiles.extend(CLOUD_MODEL_SPECS)
    return profiles

def build_router(include_cloud: bool = True) -> Router:
    profiles = build_profiles(include_cloud=include_cloud)
    return Router(profiles=profiles)

def get_role_pool() -> dict[str, list[str]]:
    return dict(ROLE_POOL)
