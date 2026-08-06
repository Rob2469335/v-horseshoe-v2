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
        self.generic_visit(node)


class SecurityGate:
    """Deterministic, immutable AST security gate for mutated code."""
    
    # BUG FIX: Expanded security banlists.
    # The original list missed dangerous builtin calls and several critical modules
    # that allow for arbitrary execution or network exfiltration.
    BANNED_CALLS = ["exec", "eval", "compile", "__import__", "open"]
    BANNED_MODULES = ["subprocess", "socket", "ctypes", "pty", "shlex"]
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
