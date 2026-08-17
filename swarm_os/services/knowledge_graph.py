import ast
import networkx as nx
from pathlib import Path
import logging

logger = logging.getLogger("KnowledgeGraph")


class DependencyVisitor(ast.NodeVisitor):
    def __init__(self, current_module):
        self.current_module = current_module
        self.dependencies = []
        self.submodule_candidates = []

    def visit_Import(self, node):
        for alias in node.names:
            self.dependencies.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.dependencies.append(node.module)
            # `from pkg import core` statically depends on `pkg.core` when that
            # submodule exists — resolve it in build_graph against the real
            # module set (never add a spurious edge for an imported NAME).
            for alias in node.names:
                if alias.name != "*" and alias.name.isidentifier():
                    self.submodule_candidates.append(f"{node.module}.{alias.name}")
        self.generic_visit(node)


class KnowledgeGraph:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()
        self.graph = nx.DiGraph()
        self._modules = set()

    def build_graph(self):
        logger.info(f"Building AST Knowledge Graph from {self.root_dir}")
        # Pass 1: register every module node (so submodule candidates can be
        # resolved against the real module set, never guessed).
        for path in self.root_dir.rglob("*.py"):
            if "site-packages" in path.parts or ".venv" in path.parts:
                continue
            module_name = (
                path.relative_to(self.root_dir)
                .with_suffix("")
                .as_posix()
                .replace("/", ".")
            )
            self._modules.add(module_name)
            self.graph.add_node(module_name, type="module", path=str(path))

        # Pass 2: parse imports and add edges (submodule candidates only when
        # the target is a real module in the tree).
        for path in self.root_dir.rglob("*.py"):
            if "site-packages" in path.parts or ".venv" in path.parts:
                continue
            try:
                module_name = (
                    path.relative_to(self.root_dir)
                    .with_suffix("")
                    .as_posix()
                    .replace("/", ".")
                )
                with open(path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(path))

                visitor = DependencyVisitor(module_name)
                visitor.visit(tree)

                for dep in visitor.dependencies:
                    self.graph.add_node(dep, type="dependency")
                    self.graph.add_edge(module_name, dep, relation="imports")
                for sub in visitor.submodule_candidates:
                    if sub in self._modules:
                        self.graph.add_node(sub, type="module")
                        self.graph.add_edge(module_name, sub, relation="imports")
            except SyntaxError:
                pass
            except Exception as e:
                logger.debug(f"Failed to parse {path}: {e}")

        logger.info(
            f"Graph built with {self.graph.number_of_nodes()} nodes and {self.graph.number_of_edges()} edges."
        )

    def query_dependencies(self, module_name: str, depth: int = 1):
        if not self.graph.has_node(module_name):
            return f"Node {module_name} not found in Knowledge Graph."

        result = []
        edges = nx.bfs_edges(self.graph, source=module_name, depth_limit=depth)
        for u, v in edges:
            result.append(f"{u} -> imports -> {v}")

        return "\n".join(result) if result else "No outgoing dependencies found."

    def query_dependents(self, module_name: str, depth: int = 1):
        if not self.graph.has_node(module_name):
            return f"Node {module_name} not found in Knowledge Graph."

        result = []
        reversed_graph = self.graph.reverse()
        edges = nx.bfs_edges(reversed_graph, source=module_name, depth_limit=depth)
        for u, v in edges:
            result.append(f"{v} -> is imported by -> {u}")

        return "\n".join(result) if result else "No incoming dependents found."

    def list_dependents(self, module_name: str, depth: int = 1) -> list[str]:
        """Return the dotted module names that (transitively, up to `depth`)
        import `module_name` — the structured form of query_dependents() for
        programmatic use (signal-2 downstream-breakage detection).

        The graph is AST-built, so this has the known dynamic-import blind spot
        (`importlib.import_module`), but it is deterministic for static imports.
        Returns [] for unknown modules (never raises)."""
        if not self.graph.has_node(module_name):
            return []
        reversed_graph = self.graph.reverse()
        edges = nx.bfs_edges(reversed_graph, source=module_name, depth_limit=depth)
        dependents = {v for _, v in edges}
        return sorted(dependents)
