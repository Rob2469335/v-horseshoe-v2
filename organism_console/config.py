import os
import time
from pathlib import Path

# ZENITH OS // 2027 Core Configuration
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BACKEND_URL = os.getenv("ZENITH_BACKEND_URL", "http://127.0.0.1:8000")
VERSION = "8.3.0"
START_TIME = time.time()
LOG_DIR = PROJECT_ROOT / "swarm_os" / "logs"
SESSION_FILE = PROJECT_ROOT / "organism_console" / ".session.json"
