import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import networkx as nx

logger = logging.getLogger(__name__)

class GraphRepository:
    def __init__(self, graph_path: Path | str = Path("logs/memory_graph.graphml")):
        self.path = Path(graph_path)
        self.graph = nx.DiGraph()
        self.lock = asyncio.Lock()
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                self.graph = nx.read_graphml(self.path)
        except Exception as exc:
            logger.warning("Failed to load graph: %s", exc)
            self.graph = nx.DiGraph()

    async def save(self) -> None:
        try:
            async with self.lock:
                self.evict_old_nodes(max_nodes=2000)
                graph_copy = self.graph.copy()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(nx.write_graphml, graph_copy, self.path)
        except Exception as exc:
            logger.warning("Failed to save graph: %s", exc)

    def evict_old_nodes(self, max_nodes: int = 2000):
        if len(self.graph.nodes) <= max_nodes:
            return
            
        pageranks = nx.pagerank(self.graph)
            
        nodes_by_rank = sorted(self.graph.nodes(), key=lambda n: pageranks.get(n, 0.0))
        nodes_to_remove = []
        for node in nodes_by_rank:
            if len(self.graph.nodes) - len(nodes_to_remove) <= max_nodes:
                break
            # Skip core structural nodes to preserve the primary graph layout
            if str(node).startswith("Session_") or str(node).startswith("Community_"):
                continue
            nodes_to_remove.append(node)
            
        for node in nodes_to_remove:
            # Topological collapse: maintain paths across evicted nodes
            preds = list(self.graph.predecessors(node))
            succs = list(self.graph.successors(node))
            for u in preds:
                for w in succs:
                    if u != w and not self.graph.has_edge(u, w):
                        self.graph.add_edge(u, w, type="collapsed_path")
            self.graph.remove_node(node)

    async def add_session_data(
        self,
        session_node: str,
        agent_node: str,
        task_node: str,
        tool_node: str,
        outcome_node: str
    ):
        async with self.lock:
            self.graph.add_node(session_node, type="Session")
            self.graph.add_node(agent_node, type="Agent")
            self.graph.add_node(task_node, type="Task")
            self.graph.add_node(tool_node, type="Tool")
            self.graph.add_node(outcome_node, type="Outcome")

            self.graph.add_edge(agent_node, session_node, relation="PARTICIPATED_IN")
            self.graph.add_edge(session_node, task_node, relation="ADDRESSED")
            self.graph.add_edge(task_node, tool_node, relation="UTILIZED")
            self.graph.add_edge(task_node, outcome_node, relation="RESULTED_IN")

    async def add_fact(self, fact_node: str, agent_node: str, details: str, ts: str):
        async with self.lock:
            self.graph.add_node(fact_node, type="Fact", content=details)
            self.graph.add_edge(agent_node, fact_node, relation="BELIEVES", timestamp=ts)

    async def add_delegation(self, receiver_node: str, agent_node: str, ts: str):
        async with self.lock:
            self.graph.add_node(receiver_node, type="Agent")
            self.graph.add_edge(receiver_node, agent_node, relation="KNOWS", timestamp=ts)
            
    def has_node(self, node: str) -> bool:
        return self.graph.has_node(node)
        
    def get_session_paths(self, session_node: str, limit: int = 15) -> List[Tuple[str, str, str, float, float]]:
        import itertools
        if not self.graph.has_node(session_node):
            return []
            
        edges = nx.edge_bfs(self.graph, session_node, orientation='original')
        pageranks = nx.get_node_attributes(self.graph, 'pagerank')
        
        paths = []
        for u, v, _ in itertools.islice(edges, limit):
            rel = self.graph[u][v].get('relation', 'CONNECTED_TO')
            score_u = pageranks.get(u, 0.0)
            score_v = pageranks.get(v, 0.0)
            paths.append((u, v, rel, score_u, score_v))
            
        return paths

    def get_node_count(self) -> int:
        return len(self.graph.nodes)

    def get_communities(self) -> List[set]:
        from networkx.algorithms import community
        undirected_graph = self.graph.to_undirected()
        return list(community.louvain_communities(undirected_graph))

    def compute_pageranks(self) -> Dict[str, float]:
        pagerank_scores = nx.pagerank(self.graph)
        nx.set_node_attributes(self.graph, pagerank_scores, 'pagerank')
        return pagerank_scores

    async def ensure_community_node(self, comm_node: str, cluster_nodes: set):
        async with self.lock:
            if not self.graph.has_node(comm_node):
                self.graph.add_node(comm_node, type="Community")
                for node in cluster_nodes:
                    self.graph.add_edge(node, comm_node, relation="BELONGS_TO")

    async def set_community_summary(self, comm_node: str, summary: str):
        async with self.lock:
            nx.set_node_attributes(self.graph, {comm_node: summary}, 'summary')
