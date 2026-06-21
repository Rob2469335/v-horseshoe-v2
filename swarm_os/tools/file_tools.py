import os
import logging

logger = logging.getLogger(__name__)

# The safe "playpen" boundary
ALLOWED_DIR = os.path.abspath(r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os")

def write_file(filepath: str, content: str) -> str:
    """
    Tool for the AI to save code directly to the local disk.
    Only allows writing inside the swarm_os directory.
    """
    try:
        # Figure out the true, full path of where the AI wants to write
        target_path = os.path.abspath(filepath)
        
        # Security Check: Does the path start with our allowed directory?
        if not target_path.startswith(ALLOWED_DIR):
            logger.error(f"SECURITY ALERT: AI tried to write outside the sandbox to {target_path}")
            return "Error: Permission denied. You are only allowed to modify files inside the swarm_os directory."

        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"AI successfully wrote to {target_path}")
        return f"Success: File saved to {target_path}"
    except Exception as e:
        logger.error(f"AI failed to write to {filepath}: {e}")
        return f"Error: Could not save file. {e}"
