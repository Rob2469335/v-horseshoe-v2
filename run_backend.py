import os

# Set environment variables BEFORE any imports
os.environ["SWARM_ANALYSIS_CLOUD"] = "off"
os.environ["SWARM_ROUTING_MODE"] = "local_only"
os.environ["SWARM_WORKSPACE_ROOT"] = r"C:\Users\rober\Projects\swe_probe_work"
os.environ["SWARM_WRITE_ROOT"] = r"C:\Users\rober\Projects\swe_probe_work"
os.environ["SWARM_MEMORY_INJECT"] = "0"
os.environ["SWARM_EVOLUTION"] = "0"
os.environ["SWARM_NO_TOASTS"] = "1"

# Now import and run uvicorn
import uvicorn

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("swarm_os.main:app", host="0.0.0.0", port=8000, log_level="info")
