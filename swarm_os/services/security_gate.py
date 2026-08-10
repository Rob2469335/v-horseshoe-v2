import ast
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger("SecurityGate")

class SecurityGateViolation(Exception):
    pass

class BannedNodeVisitor(ast.NodeVisitor):
    def __init__(self, banned_calls: List[str], banned_modules: List[str], banned_os_attrs: frozenset):
        self.banned_calls = banned_calls
        self.banned_modules = banned_modules
        self.banned_os_attrs = banned_os_attrs
        self.violations = []
        # Names bound to the os module (`import os` / `import os as o`), so a
        # later `o.system(...)` / `os.walk('.')` can be attribute-checked.
        self._os_names: set[str] = set()
        # Names bound to dangerous os attributes via `from os import system`
        # (with or without `as`), so a later `system(...)` call is caught.
        self._os_func_aliases: set[str] = set()

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id in self.banned_calls:
                self.violations.append(f"Banned built-in call found: '{node.func.id}' at line {node.lineno}")
            elif node.func.id in self._os_func_aliases:
                self.violations.append(f"Banned os call found: '{node.func.id}' at line {node.lineno}")
            # Reflection that lifts a dangerous os attribute WITHOUT a Name call
            # or a direct os.attr scan: `getattr(os, 'system')('rm -rf /')`.
            # The attr name rides as a string argument, so visit_Attribute
            # never fires. Block when the target string is a banned os attr, or
            # is a non-literal (var-driven, unverifiable → fail-closed).
            elif (
                node.func.id in ("getattr", "setattr", "delattr")
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in self._os_names
                and (
                    len(node.args) < 2
                    or not isinstance(node.args[1], ast.Constant)
                    or node.args[1].value in self.banned_os_attrs
                )
            ):
                self.violations.append(f"Banned reflection on os module: '{node.func.id}' at line {node.lineno}")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name in self.banned_modules:
                self.violations.append(f"Banned module import found: '{alias.name}' at line {node.lineno}")
            elif alias.name == "os":
                # os is allowed wholesale; dangerous ATTRIBUTES are checked in
                # visit_Attribute. Track the bound name so `import os as o`
                # still resolves for the attribute scan.
                self._os_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module in self.banned_modules:
            self.violations.append(f"Banned module import found: '{node.module}' at line {node.lineno}")
        elif node.module == "os":
            for alias in node.names:
                if alias.name in self.banned_os_attrs:
                    self.violations.append(f"Banned os import found: 'os.{alias.name}' at line {node.lineno}")
                    self._os_func_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in self._os_names
            and node.attr in self.banned_os_attrs
        ):
            self.violations.append(f"Banned os call found: 'os.{node.attr}' at line {node.lineno}")
        elif (
            isinstance(node.value, ast.Name)
            and node.value.id == "__builtins__"
            and node.attr in self.banned_calls
        ):
            # `__builtins__.exec(...)` — attribute access into the builtins
            # namespace escapes the Name-call scan (the func is an Attribute,
            # not a Name). Mirrors the existing __builtins__[...] subscript
            # block below: __builtins__ is never a legitimate sandbox target.
            self.violations.append(
                f"Banned builtins attribute call found: '__builtins__.{node.attr}' at line {node.lineno}"
            )
        self.generic_visit(node)

    def visit_Subscript(self, node):
        # `__builtins__['__import__']('os')` — indexing the builtins dict hands
        # back __import__ without a Name-call / os-attr scan ever matching. The
        # `__builtins__` name is never a legit sandbox target.
        if isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
            self.violations.append(f"Banned builtins access found: '__builtins__' at line {node.lineno}")
        # `sys.modules['os'].system('rm -rf /')` — sys.modules yields a live os
        # module whose .system attr scan never fires (value is a Subscript, not
        # a Name tracked in _os_names).
        elif (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
            and node.value.attr == "modules"
        ):
            self.violations.append(f"Banned sys.modules access found at line {node.lineno}")
        self.generic_visit(node)


class SecurityGate:
    """Deterministic, immutable AST security gate for mutated code."""
    
    # BUG FIX: Expanded security banlists.
    # The original list missed dangerous builtin calls and several critical modules
    # that allow for arbitrary execution or network exfiltration.
    BANNED_CALLS = ["exec", "eval", "compile", "__import__", "open"]
    # builtins is banned wholesale: `import builtins; builtins.exec(...)` and
    # `getattr(builtins, 'exec')` otherwise smuggle every banned call back in
    # under a module-name Attribute (the Name-call scan never fires). The
    # already-special-cased __builtins__ attribute/subscript access stays
    # blocked independently (see visit_Attribute/visit_Subscript).
    BANNED_MODULES = ["subprocess", "socket", "ctypes", "pty", "shlex", "importlib", "builtins"]
    # os/sys are NOT wholesale-banned: the debugger/coder agents legitimately run
    # `import os; os.walk('.')` / `import sys` in sandbox_repl to explore and test.
    # Only os's dangerous attributes (process exec, file destruction/mutation,
    # privilege/process control, env mutation) are blocked at the AST attribute
    # level. sys exposes nothing dangerous inside the isolated `python -I`
    # subprocess (exit/argv/print routing only). subprocess/socket/ctypes/pty/
    # shlex stay wholesale-banned — they have no safe use in a sandbox snippet.
    BANNED_OS_ATTRS = frozenset({
        # process/command execution
        "system", "popen", "posix_spawn", "posix_spawnp", "spawnl", "spawnle",
        "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
        "execlpe", "fork", "forkpty", "kill", "killpg", "startfile",
        # file destruction / mutation (read + list + walk stay allowed)
        "remove", "unlink", "rmdir", "removedirs", "rename", "replace",
        "chmod", "chown", "lchown", "truncate", "link", "symlink", "mkfifo",
        "mknod", "utime",
        # privilege / process control
        "setuid", "setgid", "seteuid", "setegid", "setreuid", "setregid",
        "setgroups", "setpgid", "setsid", "nice",
        # environment mutation
        "putenv", "unsetenv",
    })

    @classmethod
    def _scan_visitor(cls, code: str) -> BannedNodeVisitor:
        tree = ast.parse(code, mode="exec")
        visitor = BannedNodeVisitor(cls.BANNED_CALLS, cls.BANNED_MODULES, cls.BANNED_OS_ATTRS)
        visitor.visit(tree)
        return visitor

    @classmethod
    def scan_code(cls, code: str) -> None:
        """Scan inline source code (e.g. an LLM/agent-supplied Python snippet)
        before execution. Raises SecurityGateViolation on banned calls/modules."""
        try:
            visitor = cls._scan_visitor(code)
        except SyntaxError as e:
            raise SecurityGateViolation(f"Syntax Error in supplied code: {e}")
        if visitor.violations:
            violation_msg = "; ".join(visitor.violations)
            logger.error("Security Gate triggered on inline code: %s", violation_msg)
            raise SecurityGateViolation(violation_msg)
        return True

    @classmethod
    def scan_file(cls, filepath: Path):
        try:
            # BUG FIX: Read bytes directly so ast.parse respects any # coding: cookie.
            # Avoids AST execution gap where UTF-8 parse passes but execution uses CP037.
            with open(filepath, "rb") as f:
                tree = ast.parse(f.read(), filename=str(filepath))
        except SyntaxError as e:
            raise SecurityGateViolation(f"Syntax Error in {filepath}: {e}")
            
        visitor = BannedNodeVisitor(cls.BANNED_CALLS, cls.BANNED_MODULES, cls.BANNED_OS_ATTRS)
        visitor.visit(tree)
        
        if visitor.violations:
            violation_msg = "; ".join(visitor.violations)
            logger.error(f"Security Gate triggered on {filepath}: {violation_msg}")
            raise SecurityGateViolation(violation_msg)
            
        logger.info(f"Security scan passed for {filepath}.")
        return True


def clean_sandbox_env(extra: dict | None = None) -> dict:
    """Build a subprocess env for untrusted (LLM-generated) code: no API keys,
    tokens, secrets, passwords, or SWARM_* feature gates, so a malicious script
    cannot exfiltrate credentials or trigger daemon loops. Keeps PATH and the
    standard library. PYTHONNOUSERSITE=1 keeps -I isolated mode strict."""
    import os
    clean = {
        k: v for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
        and not k.startswith("SWARM_")
    }
    clean["PYTHONNOUSERSITE"] = "1"
    if extra:
        clean.update(extra)
    return clean


# L6 (2026 process-separated gate): the scanner runs in a SEPARATE process from
# the agent/exec-side so a compromised or buggy scanner does not share fate or
# memory with the thing it gates. The scanner receives the untrusted code on
# stdin and returns its decision ONLY via exit code + stderr — there is no
# verdict document to parse or spoof, so a malformed/garbage response cannot
# fallback-guess into an allow. Fail-closed: spawn error / timeout / non-zero
# exit are all DENY with an explicit reason.
_SCAN_RUNNER = r"""
import sys
sys.path.insert(0, {project_root!r})
from swarm_os.services.security_gate import SecurityGate, SecurityGateViolation
code = sys.stdin.read()
try:
    SecurityGate.scan_code(code)
except SecurityGateViolation as e:
    sys.stderr.write("DENY: %s" % e)
    sys.exit(1)
sys.exit(0)
"""


def scan_code_isolated(code: str, project_root: str | None = None,
                       timeout: float = 20.0) -> tuple[bool, str]:
    """Run the AST security scan for untrusted code in a SEPARATE process and
    return (ok, reason).

    Returns (True, "") when the scan PASSES (exit 0). Otherwise returns
    (False, reason) with an explicit reason — never a generic "passed". A crash
    (non-zero exit), a spawn error, or a timeout are all DENY. Never raises."""
    import os
    import subprocess
    import sys
    if project_root is None:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    runner = _SCAN_RUNNER.format(project_root=project_root)
    cmd = [sys.executable, "-I", "-c", runner]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_sandbox_env(),
        )
    except Exception as exc:
        return (False, f"Security gate scan process could not start: {exc}")
    try:
        _, err_bytes = proc.communicate(input=code.encode("utf-8", errors="replace"), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()
        return (False, "Security gate scan timed out; execution denied.")
    # Fail-closed hardening: only a REAL integer exit code of 0 is an allow. A
    # mocked/unusual returncode (test harness), a None, or a non-int (a crashed
    # scanner object) must DENY — never fall through as if the scan passed.
    rc = proc.returncode
    if isinstance(rc, int) and rc == 0:
        return (True, "")
    err = err_bytes.decode("utf-8", errors="replace").strip()
    reason = err or f"security gate scan failed (exit {rc!r})"
    return (False, f"Security Gate blocked execution: {reason}")
