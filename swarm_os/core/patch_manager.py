import os
import subprocess
from typing import List

class PatchManager:
    """
    Handles all Git and File System operations for the Patch Lifecycle.
    Strictly validates all subprocess returns.
    """
    def __init__(self, repo_path: str = None):
        self.repo_path = repo_path or os.getcwd()

    def _run(self, cmd: List[str]) -> str:
        result = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\nStderr: {result.stderr}")
        return result.stdout.strip()

    def assert_clean_repo(self):
        status = self._run(["git", "status", "--porcelain"])
        if status:
            raise RuntimeError("Repository is not clean. Commit or stash changes first.")

    def get_current_branch(self) -> str:
        return self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"])

    def create_isolation_branch(self, patch_id: str) -> str:
        branch_name = f"swarm/patch-{patch_id}"
        # Force delete if exists to ensure clean start
        try:
            self._run(["git", "branch", "-D", branch_name])
        except Exception:
            pass
        self._run(["git", "checkout", "-b", branch_name])
        return branch_name

    def apply_patch_diff(self, diff: str):
        """Applies a diff. Supports REPLACE_ALL::path\ncontent or standard diff."""
        if "REPLACE_ALL::" in diff:
            parts = diff.split("REPLACE_ALL::")
            for part in parts[1:]:
                lines = part.splitlines()
                if not lines: continue
                path = lines[0].strip()
                content = "\n".join(lines[1:])
                abs_path = os.path.join(self.repo_path, path)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(content)
        else:
            patch_file = os.path.join(self.repo_path, ".swarm_temp.patch")
            with open(patch_file, "w", encoding="utf-8") as f:
                f.write(diff)
            try:
                self._run(["git", "apply", ".swarm_temp.patch"])
            finally:
                if os.path.exists(patch_file):
                    os.remove(patch_file)

    def commit_changes(self, message: str):
        self._run(["git", "add", "."])
        self._run(["git", "commit", "-m", message])

    def rollback(self, base_branch: str, feature_branch: str):
        self._run(["git", "checkout", base_branch])
        try:
            self._run(["git", "branch", "-D", feature_branch])
        except Exception:
            pass
