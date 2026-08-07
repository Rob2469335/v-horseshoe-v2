"""Loader for the written autonomy policy (autonomy_policy.json at repo root).

2026 Agentic Delegation Policy: the ceiling of what the autonomous self-healing
layer may do without a human in the loop is ONE versioned, machine-readable
artifact. This loader is the single entry point that both the repair engine and
the (future) server-side watch-loop use at startup, so the written ceiling is
what actually gets enforced — and so the code can never drift from the written
policy again (e.g. the `src/` staleness that previously lived in a duplicated
constant).

The self-modify block set is computed two ways, deliberately:
  1. DIRECTORY-LEVEL: any path under a blocked directory (healing/, etc.) is
     self-modify, regardless of filename — survives file layout refactors.
  2. DEPENDENCY-AWARE: any project file whose module is imported by the repair
     machinery (repair_engine, self_repair_engine, healing_watchman,
     security_gate, and the healing/watch modules) is self-modify, even if it
     lives inside an otherwise-allowed directory. A new helper file that the
     repair engine starts importing becomes self-modify automatically.

Failure mode being closed: someone adds a new file inside swarm_os/ that the
repair engine imports — an enumerated path list would miss it; this rule catches
it because it's derived from the actual import graph at load time.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

log = logging.getLogger("AutonomyPolicy")

POLICY_FILE = Path(__file__).resolve().parent.parent.parent / "autonomy_policy.json"

# Project files that ARE the repair/watch machinery — we compute their imports
# to derive the dependency-aware self-modify set. The policy JSON's explicit
# blocked_paths are the directory-level anchors; this list is the import roots.
_MACHINERY_MODULES = (
    "organism_console.core.repair_engine",
    "organism_console.core.self_repair_engine",
    "organism_console.core.healing_watchman",
    "swarm_os.services.security_gate",
    "swarm_os.healing.healing_loop",
    "swarm_os.healing.recovery_engine",
    "swarm_os.healing.failure_detector",
    "swarm_os.healing.governor",
    "swarm_os.services.reflection_loop",
    "swarm_os.services.danger_room",
)


class AutonomyPolicy:
    def __init__(self, data: dict, repo_root: Path):
        self.data = data
        self.repo_root = repo_root
        self.repair_allowed_dirs = [repo_root / d for d in data["scope_ceiling"]["repair_allowed_dirs"]]
        self.blocked_patterns = tuple(data["scope_ceiling"]["blocked_patterns"])
        self.never_route_to = tuple(data["scope_ceiling"]["hard_model_boundary"]["never_route_to"])
        self.per_incident_budget = int(data["watch_loop"]["per_incident_repair_budget"])
        self.daily_budget = int(data["watch_loop"]["daily_repair_budget"])
        self.self_modify_files = self._compute_self_modify_files()

    def _compute_self_modify_files(self) -> set:
        """Directory-level blocked paths + any project file the machinery imports."""
        files: set = set()
        for blocked in self.data["scope_ceiling"]["never_self_modify"]["blocked_paths"]:
            p = self.repo_root / blocked
            if p.is_dir():
                files.update(str(f.resolve()) for f in p.rglob("*.py"))
            else:
                files.add(str(p.resolve()))
        for f in self._machinery_imports():
            files.add(str(f.resolve()))
        return files

    def _machinery_imports(self) -> set:
        """Walk the machinery modules' own project imports (recursively) and
        resolve each imported project module to its file path. A file the repair
        machinery imports is treated as self-modify even if it lives in an
        otherwise-allowed directory. Only EXACT project modules are scanned —
        no package-root expansion, so a machinery module importing
        `from swarm_os.services import vector_store` resolves precisely to
        vector_store.py, and does NOT drag in the whole `swarm_os` package."""
        files: set = set()
        seen: set = set()

        def resolve(module: str) -> Path | None:
            parts = module.split(".")
            candidate = self.repo_root / Path(*parts).with_suffix(".py")
            if candidate.exists():
                return candidate
            pkg = self.repo_root / Path(*parts) / "__init__.py"
            return pkg if pkg.exists() else None

        def is_project(mod: str) -> bool:
            return (mod.startswith("swarm_os")
                    or mod.startswith("runtime_v2")
                    or mod.startswith("organism_console"))

        def scan(mod_name: str) -> None:
            if mod_name in seen or not is_project(mod_name):
                return
            seen.add(mod_name)
            path = resolve(mod_name)
            if path is None:
                return
            files.add(path)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                return
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    # `from pkg.mod import sub` -> submodule lives in node.names.
                    scan(node.module)
                    if node.module and is_project(node.module):
                        for alias in node.names[:1]:
                            scan(f"{node.module}.{alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        scan(alias.name)

        for m in _MACHINERY_MODULES:
            scan(m)
        return files

    def is_repairable(self, file_path: Path | None) -> bool:
        """True if the autonomous layer may repair this file unsupervised."""
        if not file_path:
            return False
        try:
            resolved = Path(file_path).resolve()
        except Exception:
            return False
        if resolved.suffix != ".py":
            return False
        if str(resolved) in self.self_modify_files:
            return False
        parts = [p.lower() for p in resolved.parts]
        for pattern in self.blocked_patterns:
            if any(pattern in p for p in parts):
                return False
        for allowed in self.repair_allowed_dirs:
            try:
                if str(resolved).startswith(str(Path(allowed).resolve())):
                    return True
            except Exception:
                continue
        return False


_policy_cache: AutonomyPolicy | None = None


def get_autonomy_policy(reload: bool = False) -> AutonomyPolicy | None:
    """Load (and cache) the autonomy policy. Returns None if the file is missing
    or malformed — callers must fail CLOSED on None (treat as 'not allowed')."""
    global _policy_cache
    if _policy_cache is not None and not reload:
        return _policy_cache
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        root = Path(__file__).resolve().parent.parent.parent
        _policy_cache = AutonomyPolicy(data, root)
        return _policy_cache
    except Exception as exc:
        log.warning("Failed to load autonomy policy (%s); enforcing fail-closed None.", exc)
        _policy_cache = None
        return None
