$env:SWARM_ANALYSIS_CLOUD = "off"
$env:SWARM_ROUTING_MODE = "local_only"
$env:SWARM_WORKSPACE_ROOT = "C:\Users\rober\Projects\swe_probe_work\repair_task1"
$env:SWARM_WRITE_ROOT = "C:\Users\rober\Projects\swe_probe_work\repair_task1"
$env:SWARM_MEMORY_INJECT = "0"
$env:SWARM_EVOLUTION = "0"
$env:SWARM_NO_TOASTS = "1"
$env:SWARM_ANALYSIS_CLOUD = "off"
$env:SWARM_ROUTING_MODE = "local_only"

cd "C:\Users\rober\Projects\v-horseshoe-v2"
.venv\Scripts\python.exe -m uvicorn swarm_os.main:app --port 8000 --log-level info
