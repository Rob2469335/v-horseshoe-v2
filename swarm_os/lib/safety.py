import subprocess

def create_snapshot(message: str):
    """
    Creates a hidden checkpoint of the swarm's state before refactoring.
    """
    subprocess.run(["git", "add", "."])
    subprocess.run(["git", "commit", "-m", f"SNAPSHOT: {message}"])
    print(f"Snapshot created: {message}")
