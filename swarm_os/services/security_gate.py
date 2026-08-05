import ast
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger("SecurityGate")

class SecurityGateViolation(Exception):
    pass

class BannedNodeVisitor(ast.NodeVisitor):
    def __init__(self, banned_calls: List[str], banned_modules: List[str]):
        self.banned_calls = banned_calls
        self.banned_modules = banned_modules
        self.violations = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in self.banned_calls:
            self.violations.append(f"Banned built-in call found: '{node.func.id}' at line {node.lineno}")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name in self.banned_modules:
                self.violations.append(f"Banned module import found: '{alias.name}' at line {node.lineno}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module in self.banned_modules:
            self.violations.append(f"Banned module import found: '{node.module}' at line {node.lineno}")
        self.generic_visit(node)


class SecurityGate:
    """Deterministic, immutable AST security gate for mutated code."""
    
    # BUG FIX: Expanded security banlists.
    # The original list missed dangerous builtin calls and several critical modules
    # that allow for arbitrary execution or network exfiltration.
    BANNED_CALLS = ["exec", "eval", "compile", "__import__", "open"]
    BANNED_MODULES = ["subprocess", "os.system", "os", "sys", "socket", "ctypes", "pty", "shlex"]

    @classmethod
    def _scan_visitor(cls, code: str) -> BannedNodeVisitor:
        tree = ast.parse(code, mode="exec")
        visitor = BannedNodeVisitor(cls.BANNED_CALLS, cls.BANNED_MODULES)
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
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(filepath))
        except SyntaxError as e:
            raise SecurityGateViolation(f"Syntax Error in {filepath}: {e}")
            
        visitor = BannedNodeVisitor(cls.BANNED_CALLS, cls.BANNED_MODULES)
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
