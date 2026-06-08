import importlib

from swarm_os.self_heal import SelfHeal

CRITICAL_MODULES = [
    "swarm_os.services.orchestrator",
    "swarm_os.services.control_plane.router",
    "swarm_os.services.control_plane.models",
]

def validate_import_graph():
    failed = []
    healer = None

    for m in CRITICAL_MODULES:
        try:
            importlib.import_module(m)
        except Exception as e:
            failed.append((m, str(e)))

    if failed:
        print("IMPORT GRAPH FAILURE - ATTEMPTING SELF HEAL")
        healer = SelfHeal()
        for f in failed:
            print(f)
        raise ImportError("Locked import graph violation detected")

    print("IMPORT GRAPH LOCKED ✔")

