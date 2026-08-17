"""AST-based dependency analysis for forward/reverse import resolution."""

import ast
from pathlib import Path
from typing import List, Optional, Set, Tuple


class ImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports = []

    def visit_Import(self, node: ast.Import):
        for name in node.names:
            self.imports.append((name.name, 0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.append((node.module, node.level or 0))
            for alias in node.names:
                self.imports.append((f"{node.module}.{alias.name}", node.level or 0))
        self.generic_visit(node)


def resolve_module_path(
    module_name: str, level: int, current_file: Path, project_root: Path
) -> Optional[Path]:
    try:
        if level > 0:
            base_dir = current_file.parent
            for _ in range(level - 1):
                if base_dir.parent == base_dir:
                    break
                base_dir = base_dir.parent
            if module_name:
                rel_path = base_dir / module_name.replace(".", "/")
            else:
                rel_path = base_dir
        else:
            if not module_name:
                return None
            rel_path = project_root / module_name.replace(".", "/")

        py_file = rel_path.with_suffix(".py")
        if py_file.exists() and py_file.is_file():
            return py_file.resolve()

        init_file = rel_path / "__init__.py"
        if init_file.exists() and init_file.is_file():
            return init_file.resolve()

        if rel_path.exists() and rel_path.is_dir():
            return rel_path.resolve()
    except Exception:
        pass
    return None


def get_forward_dependencies(
    file_path: Path,
    project_root: Path,
    depth: int = 1,
    max_depth: int = 3,
    visited: Optional[Set[Path]] = None,
) -> List[Tuple[Path, int]]:
    if visited is None:
        visited = set()
    if file_path in visited or depth > max_depth:
        return []
    visited.add(file_path)

    deps = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content)
        visitor = ImportVisitor()
        visitor.visit(tree)
        for mod, lvl in visitor.imports:
            resolved = resolve_module_path(mod, lvl, file_path, project_root)
            if resolved and resolved.exists() and resolved != file_path:
                deps.append((resolved, depth))
                deps.extend(
                    get_forward_dependencies(
                        resolved, project_root, depth + 1, max_depth, visited
                    )
                )
    except Exception:
        pass
    return deps


def get_reverse_dependencies(target_file: Path, project_root: Path) -> List[Path]:
    rev_deps = []
    target_resolved = target_file.resolve()
    target_stem = target_file.stem

    for py_file in project_root.rglob("*.py"):
        if (
            ".venv" in py_file.parts
            or "tests" in py_file.parts
            or "__pycache__" in py_file.parts
        ):
            continue
        if py_file.resolve() == target_resolved:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if target_stem not in content:
                continue

            tree = ast.parse(content)
            visitor = ImportVisitor()
            visitor.visit(tree)
            for mod, lvl in visitor.imports:
                resolved = resolve_module_path(mod, lvl, py_file, project_root)
                if resolved and resolved.resolve() == target_resolved:
                    rev_deps.append(py_file)
                    break
        except Exception:
            pass
    return rev_deps
