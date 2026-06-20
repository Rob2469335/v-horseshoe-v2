import subprocess
import os
from typing import Dict, Any

class CIEngine:
    """
    Executes CI validation suite. 
    Returns structured result objects.
    """
    def __init__(self, repo_path: str = None):
        self.repo_path = repo_path or os.getcwd()

    def run_suite(self) -> Dict[str, Any]:
        return {
            "compile": self._check_compile(),
            "tests": self._run_tests(),
            "lint": self._run_lint()
        }

    def _check_compile(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["python", "-m", "compileall", "-q", "."],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            return {"status": "ok" if res.returncode == 0 else "error", "output": res.stderr}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _run_tests(self) -> Dict[str, Any]:
        try:
            # -q: quiet, --maxfail=5: stop after 5 failures
            res = subprocess.run(
                ["pytest", "-q", "--maxfail=5"],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            # 0: all passed, 5: no tests found (count as ok for skeleton)
            return {
                "status": "ok" if res.returncode in (0, 5) else "fail",
                "exit_code": res.returncode,
                "summary": res.stdout[-500:] # Last 500 chars
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _run_lint(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["ruff", "check", "."],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            return {"status": "ok" if res.returncode == 0 else "warn", "output": res.stdout[:500]}
        except:
            return {"status": "skipped"}
