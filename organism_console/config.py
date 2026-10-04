import os
import time
from pathlib import Path

# ZENITH OS // 2027 Core Configuration

# CODE_ROOT is where the console code lives. It is NEVER the task workspace.
CODE_ROOT = Path(__file__).parent.parent.resolve()


def _resolve_task_root() -> Path:
    """The root the console's TASK operations target.

    ``SWARM_WORKSPACE_ROOT`` when set -- the authoritative isolated task
    workspace, the same root ``swarm_os.lib.paths.agent_workspace_root`` gives
    the filesystem/sandbox tools -- else the code root. When the env is set it
    is honored even if it does not exist; there is no silent fall-back to the
    main repository (the tools validate existence and fail closed).
    """
    env_root = os.getenv("SWARM_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return CODE_ROOT


# TASK-facing root: the isolated arm workspace when one is declared, else the
# code root (unchanged behaviour when no isolated workspace is in play).
PROJECT_ROOT = _resolve_task_root()
BACKEND_URL = os.getenv("ZENITH_BACKEND_URL", "http://127.0.0.1:8000")
VERSION = "8.3.0"
START_TIME = time.time()
# Repo-owned state (logs, session) stays at the CODE root, never the task
# workspace, so an isolated arm's tree is not polluted by console state.
LOG_DIR = CODE_ROOT / "swarm_os" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = CODE_ROOT / "organism_console" / ".session.json"
SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
