import subprocess
import os

def create_snapshot(message: str):
    """
    Creates a hidden checkpoint of the swarm's state before refactoring.
    """
    subprocess.run(["git", "add", "."])
    subprocess.run(["git", "commit", "-m", f"SNAPSHOT: {message}"])
    print(f"Snapshot created: {message}")

def validate_path(target_path: str, workspace_root: str = None) -> str:
    """
    Strict guardrail: Raises a ValueError if the swarm tries to access
    a file outside of the allowed project root folder.
    """
    if workspace_root is None:
        # Default to the current project directory layout
        workspace_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        
    abs_target = os.path.abspath(target_path)
    abs_root = os.path.abspath(workspace_root)
    
    # Check if the target path starts with the allowed root directory path string
    if not abs_target.startswith(abs_root):
        raise ValueError(f"SECURITY VIOLATION: Target path '{abs_target}' is outside workspace root '{abs_root}'!")
        
    return abs_target

