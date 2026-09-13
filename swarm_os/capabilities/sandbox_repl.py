from __future__ import annotations
import asyncio
import sys


class SandboxReplHandler:
    async def execute(self, payload) -> dict:
        if isinstance(payload, dict):
            language = payload.get("language", "python")
            code = payload.get("code", "")
            command = payload.get("command", "")
            path = payload.get("path", "")
        else:
            language = getattr(payload, "language", "python")
            code = getattr(payload, "code", "")
            command = getattr(payload, "command", "")
            path = getattr(payload, "path", "")

        language = str(language).lower().strip()

        if language == "python":
            # SECURITY: AST-scan LLM/agent-supplied code before execution. The
            # sandbox is NOT a real isolation boundary (runs as the user, cwd=root),
            # so block banned calls/modules (exec/eval/subprocess/os/sys/socket/...)
            # deterministically instead of pretending it is safe.
            # 2026 L6: the scan itself runs in a SEPARATE process (scan_code_isolated)
            # so a buggy/compromised scanner does not share fate with the exec-side.
            # Fail-closed: any scan error/timeout/non-zero exit denies execution with
            # an explicit reason (never a generic "passed").
            try:
                from swarm_os.services.security_gate import scan_code_isolated

                scan_ok, scan_reason = await asyncio.to_thread(
                    scan_code_isolated, str(code)
                )
                if not scan_ok:
                    return {
                        "ok": False,
                        "stdout": "",
                        "stderr": scan_reason,
                        "error": scan_reason,
                        "returncode": 1,
                    }
            except Exception as e:
                # Fail closed: a scan subprocess failure must DENY, never fall
                # through to execution (a malformed scan response must not be
                # misread as "scan passed").
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Security gate unavailable, execution denied: {e}",
                    "error": f"Security gate unavailable, execution denied: {e}",
                    "returncode": 1,
                }
            cmd = [sys.executable, "-I", "-c", str(code)]
            timeout = 30.0
        elif language == "powershell":
            # SECURITY: PowerShell has no clean AST-scan analog here, so gate the
            # command string with a conservative denylist of destructive/system-
            # mutating verbs + the shapes that dodge a verb denylist (aliases,
            # backtick escapes, iex, .NET interop, call operator, separators).
            # NOTE: a denylist is NOT a sandbox boundary — it is defense-in-depth
            # on top of the ALWAYS_CONFIRM approval gate in the agent path.
            blocked_ps = (
                # destructive / system-mutating verbs (normalized: no '-', no
                # backticks, so `Remove-Item` and `Remove`-`Item` both match)
                "remove",
                "rm",
                "stop",
                "set",
                "format",
                "new",
                "start",
                "restart",
                "del",
                "rd",
                "erase",
                "kill",
                "taskkill",
                "shutdown",
                "restartcomputer",
                "reg delete",
                "diskpart",
                "takeown",
                "icacls",
                "attrib",
                "install",
                "uninstall",
                "outfile",
                "setcontent",
                "addcontent",
                "copyitem",
                "moveitem",
                "renameitem",
                "clearitem",
                "removeitem",
                # alias/call-operator shapes that reach the same verbs
                "ri ",
                "mv ",
                "cp ",
                "ni ",
                "ii ",
                "saps ",
                "iex ",
                "iex(",
                "& ",
                # .NET / WMI interop (no verb to match)
                "[system.",
                "[diagnostics.",
                "[wmi",
                "::delete",
                "::start(",
                ".net",
                "invokeexpression",
                "invokecommand",
                # separators / redirection that chain or escape
                ">",
                ">>",
                "|",
                ";",
                "`",
            )
            ps_lower = str(command or "").lower().replace("-", "").replace("`", "")
            if any(b in ps_lower for b in blocked_ps):
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Security Gate blocked PowerShell command (destructive/system-mutating operations are not allowed).",
                    "returncode": 1,
                }
            cmd = ["pwsh", "-NoProfile", "-Command", str(command)]
            timeout = 30.0
        elif language == "pytest":
            # SECURITY: a payload/LLM-controlled path must not smuggle pytest
            # CLI options (--junitxml=..., --pdb, -x). Reject any '-'/-leading
            # target, enforce sandbox containment, and append '--' so the rest
            # are parsed as files, never options.
            from pathlib import Path as _Path

            raw = str(path or "")
            if not raw or raw.startswith("-"):
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Security Gate blocked pytest target (flag-like or empty path).",
                    "returncode": 1,
                }
            project_root = _Path(__file__).resolve().parents[2]
            try:
                _Path(raw).resolve().relative_to(project_root.resolve())
            except (ValueError, OSError):
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "Security Gate blocked pytest target (outside project root).",
                    "returncode": 1,
                }
            cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short", "--", str(raw)]
            timeout = 60.0
        else:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Unsupported language: {language}",
                "returncode": 1,
            }

        import os
        from swarm_os.services.security_gate import clean_sandbox_env

        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )

        env_extra = {}
        if language == "pytest":
            import site

            user_site = site.getusersitepackages()
            env_extra["PYTHONNOUSERSITE"] = "0"
            existing_pp = os.environ.get("PYTHONPATH", "")
            env_extra["PYTHONPATH"] = (
                f"{user_site}{os.pathsep}{existing_pp}" if user_site else existing_pp
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_root,
                env=clean_sandbox_env(extra=env_extra),
            )
            try:
                async with asyncio.timeout(timeout):
                    stdout_bytes, stderr_bytes = await proc.communicate()
                return {
                    "ok": proc.returncode == 0,
                    "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                    "stderr": stderr_bytes.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode if proc.returncode is not None else 0,
                }
            except TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                await proc.wait()
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Execution timed out ({timeout}s limit).",
                    "returncode": -1,
                }
            finally:
                # asyncio.CancelledError inherits BaseException — the except
                # TimeoutError above never fires on a cancelled/abandoned stream,
                # so without this an orphaned `python -I` keeps running (up to
                # the full timeout). Kill any proc we did not finish.
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    await proc.wait()
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": str(e), "returncode": 1}
