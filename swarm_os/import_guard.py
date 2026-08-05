import importlib

CRITICAL_IMPORTS = [
    "swarm_os.services.orchestrator",
    "swarm_os.services.control_plane.router",
    "swarm_os.services.control_plane.models",
]

def validate():
    for module in CRITICAL_IMPORTS:
        importlib.import_module(module)
    print("IMPORT GRAPH OK")

