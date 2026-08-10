import shutil
import uuid
import logging
import asyncio
from pathlib import Path
from typing import List

from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation

logger = logging.getLogger("DangerRoom")

class DangerRoom:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.sandbox_id = str(uuid.uuid4())[:8]
        self.sandbox_dir = self.root_dir / f".sandbox_{self.sandbox_id}"
        self.is_active = False
        
        # RLVR: Shield the fitness function directory
        self.shielded_dir = self.root_dir / "swarm_os" / "evals"

    async def setup(self) -> Path:
        logger.info(f"Setting up Danger Room sandbox at {self.sandbox_dir}")
        def ignore_func(_dir_path, contents):
            return [c for c in contents if c in ('.git', '.venv', '__pycache__', 'node_modules', 'nul', 'audit_test.py') or c.startswith('.sandbox') or c.startswith('.gemini')]
            
        await asyncio.to_thread(shutil.copytree, self.root_dir, self.sandbox_dir, ignore=ignore_func)
        self.is_active = True
        
        # Ensure shielded dir exists in sandbox if not in root
        sandbox_evals = self.sandbox_dir / "swarm_os" / "evals"
        sandbox_evals.mkdir(parents=True, exist_ok=True)
        
        return self.sandbox_dir

    async def scan_sandbox(self, specific_files: List[str] = None):
        """Phase 2: The Deterministic Control Layer scans the sandbox before tests can run."""
        if not self.is_active:
            raise RuntimeError("Sandbox is not active")
            
        logger.info("Executing AST Security Scan on sandbox mutations...")
        
        files_to_scan = []
        if specific_files:
            files_to_scan = [self.sandbox_dir / f for f in specific_files]
        else:
            files_to_scan = list(self.sandbox_dir.rglob("*.py"))
            
        for path in files_to_scan:
            try:
                if path.exists():
                    await asyncio.to_thread(SecurityGate.scan_file, path)
            except SecurityGateViolation as e:
                logger.error(f"FATAL: Sandbox mutation violated security policies in {path.name}: {e}")
                await self.teardown()
                raise e

    async def run_tests(self, test_targets: List[str] = None,
                        timeout: float = 120.0) -> dict:
        """Run pytest against test targets INSIDE the sandbox and return a REAL
        exit code (2026 L3: real test_pass replaces the completion-proxy of
        outcome_fitness).

        The sandbox copy already happened in setup(); scanning (AST security
        gate) is the caller's responsibility via scan_sandbox(). Returns:
          {"exit_code": int, "ok": bool (exit_code==0), "output": str}
        Fails-closed on any setup/subprocess error (ok=False). Never raises."""
        if not self.is_active:
            return {"exit_code": -1, "ok": False, "output": "sandbox not active"}

        import sys as _sys
        cmd = [_sys.executable, "-m", "pytest", "-q", "--tb=line"]
        if test_targets:
            validated: List[str] = []
            for t in test_targets:
                raw = str(t)
                # A flag-like target (--junitxml=..., -x, -q) would be parsed by
                # pytest as a CLI option, not a file — an injected pytest flag
                # can make the sandbox test run behave arbitrarily (e.g. write
                # outside the sandbox). Reject rather than pass through.
                if raw.startswith("-"):
                    logger.warning("[DangerRoom] Rejected flag-like test target: %s", t)
                    continue
                p = Path(raw)
                try:
                    p.resolve().relative_to(self.sandbox_dir.resolve())
                except ValueError:
                    logger.warning("[DangerRoom] Rejected test target outside sandbox: %s", t)
                    continue
                validated.append(raw)
            if validated:
                cmd += ["--"] + validated
        try:
            from swarm_os.services.security_gate import clean_sandbox_env
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.sandbox_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=clean_sandbox_env(),
            )
            try:
                async with asyncio.timeout(timeout):
                    out, _ = await proc.communicate()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {"exit_code": -1, "ok": False,
                        "output": f"test run timed out after {timeout}s (sandbox)"}
            finally:
                # CancelledError inherits BaseException — the except TimeoutError
                # above never fires on a task cancellation, so without a finally
                # an abandoned test subprocess would be orphaned (still running
                # pytest inside the sandbox). Kill any proc we did not finish.
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            text = out.decode("utf-8", errors="replace")
            return {
                "exit_code": proc.returncode,
                "ok": proc.returncode == 0,
                "output": text[-4000:],
            }
        except Exception as exc:
            logger.warning("DangerRoom test run failed: %s", exc)
            return {"exit_code": -1, "ok": False, "output": f"test run error: {exc}"}

    async def merge_back(self, relative_files: List[str]):
        if not self.is_active:
            raise RuntimeError("Sandbox is not active")
            
        logger.info(f"Merging {len(relative_files)} files back to main workspace from Danger Room...")
        for rel_file in relative_files:
            if "swarm_os/evals" in rel_file.replace("\\", "/"):
                logger.critical(f"SHIELD VIOLATION: Attempted merge into shielded fitness directory: {rel_file}")
                await self.teardown()
                raise SecurityGateViolation(f"Agents are strictly prohibited from modifying evals: {rel_file}")
                
            src = (self.sandbox_dir / rel_file).resolve()
            dst = (self.root_dir / rel_file).resolve()
            
            # BUG FIX: Directory Traversal Protection
            # Without this check, a malicious payload could pass `../../windows/system32/cmd.exe`
            # and `shutil.copy2` would happily overwrite files outside the sandbox.
            try:
                src.relative_to(self.sandbox_dir.resolve())
                dst.relative_to(self.root_dir.resolve())
            except ValueError:
                logger.critical(f"SANDBOX ESCAPE ATTEMPT: Directory traversal detected: {rel_file}")
                await self.teardown()
                raise SecurityGateViolation(f"Sandbox escape attempt blocked: {rel_file}")

            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, src, dst)
                logger.info(f"Merged: {rel_file}")
            else:
                logger.warning(f"Failed to merge {rel_file}: File not found in sandbox")

    async def teardown(self):
        if self.sandbox_dir.exists():
            logger.info(f"Tearing down Danger Room sandbox {self.sandbox_dir}")
            await asyncio.to_thread(shutil.rmtree, self.sandbox_dir, ignore_errors=True)
        self.is_active = False

    async def __aenter__(self):
        await self.setup()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.teardown()
