"""Edit-personalized symbol context (dependency-free repo-map slice).

Research grounding (aider repo-map / agentpatterns.ai "Repository Map
Pattern"): before editing, an agent needs the *signatures* that exist in and
around a file, not full bodies. aider builds this with tree-sitter + PageRank;
this module is the dependency-free analogue for this ~95%-Python codebase,
using the stdlib ``ast`` module (already used by knowledge_graph/security_gate).

Two pure-ish helpers:
  * ``extract_symbol_map(source)`` — compact class/function signature map of a
    single file's source. Pure: str -> str, never raises.
  * ``find_direct_importers(path, root)`` — modules that import ``path``'s
    module, via the existing AST ``KnowledgeGraph`` (cached per root, so the
    O(repo) build happens once per process, then lookups are O(1)).

Both degrade to empty results on any failure (never break a read).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_EXCLUDE_PARTS = {".venv", ".git", "__pycache__", "node_modules", "site-packages"}


def extract_symbol_map(source: str, max_symbols: int = 40) -> str:
    """Return a compact, indented symbol map (classes + def signatures) for a
    Python source string. Pure function; returns "" on any parse failure."""
    if not source:
        return ""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError):
        return ""

    lines: list[str] = []

    def _sig(node) -> str:
        try:
            args = [a.arg for a in node.args.args]
            if node.args.vararg:
                args.append("*" + node.args.vararg.arg)
            if node.args.kwarg:
                args.append("**" + node.args.kwarg.arg)
            return f"{node.name}({', '.join(args)})"
        except Exception:
            return node.name

    def _walk(body, indent: int) -> None:
        for node in body:
            if len(lines) >= max_symbols:
                return
            pad = "  " * indent
            if isinstance(node, ast.ClassDef):
                lines.append(f"{pad}class {node.name}")
                _walk(node.body, indent + 1)
            elif isinstance(node, ast.AsyncFunctionDef):
                lines.append(f"{pad}async def {_sig(node)}")
            elif isinstance(node, ast.FunctionDef):
                lines.append(f"{pad}def {_sig(node)}")

    _walk(tree.body, 0)
    return "\n".join(lines)


def module_name_for_path(path: Path, root: Path) -> str:
    """Dotted module name for a .py path relative to root ('' if outside)."""
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve())
        return rel.with_suffix("").as_posix().replace("/", ".")
    except (ValueError, OSError):
        return ""


_KG_CACHE: dict[str, object] = {}


def find_direct_importers(path: Path, root: Path, max_results: int = 8) -> list[str]:
    """Modules that directly import ``path``'s module.

    Reuses the AST ``KnowledgeGraph`` (cached per root so the O(repo) build runs
    once per process). Returns [] on any failure (never raises)."""
    module = module_name_for_path(path, root)
    if not module:
        return []
    try:
        from swarm_os.services.knowledge_graph import KnowledgeGraph

        key = str(Path(root).resolve())
        kg = _KG_CACHE.get(key)
        if kg is None:
            kg = KnowledgeGraph(key)
            kg.build_graph()
            _KG_CACHE[key] = kg
        return kg.list_dependents(module, depth=1)[:max_results]
    except Exception as e:  # noqa: BLE001 - degrade, never break a read
        logger.debug("find_direct_importers failed for %s: %s", path, e)
        return []


def symbol_context_for_read(path: Path, content: str, root: Path) -> dict:
    """Build the additive symbol-context payload for a .py read.

    Returns {} for non-Python files or on any failure (caller merges it into the
    read result; an empty dict leaves the result unchanged)."""
    try:
        p = Path(path)
        if p.suffix != ".py":
            return {}
        sym = extract_symbol_map(content)
        out: dict = {}
        if sym:
            out["symbol_map"] = sym
        importers = find_direct_importers(p, root)
        if importers:
            out["importers"] = importers
        return out
    except Exception as e:  # noqa: BLE001 - degrade, never break a read
        logger.debug("symbol_context_for_read failed for %s: %s", path, e)
        return {}
