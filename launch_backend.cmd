@echo off
set SWARM_ANALYSIS_CLOUD=off
set SWARM_ROUTING_MODE=local_only
set SWARM_WORKSPACE_ROOT=C:\Users\rober\Projects\swe_probe_work\repair_task1
set SWARM_WRITE_ROOT=C:\Users\rober\Projects\swe_probe_work\repair_task1
set SWARM_MEMORY_INJECT=0
set SWARM_EVOLUTION=0
set SWARM_NO_TOASTS=1
set SWARM_ANALYSIS_CLOUD=off
set SWARM_ROUTING_MODE=local_only
cd /d C:\Users\rober\Projects\v-horseshoe-v2
.venv\Scripts\python.exe -m uvicorn swarm_os.main:app --port 8000 --log-level info
