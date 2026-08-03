import os
import json
import uuid
import requests
from typing import List, Dict, Any, Optional

EMBED_URL = os.getenv("EMBED_URL", os.getenv("LLAMA_URL", os.getenv("OLLAMA_URL", "http://127.0.0.1:8081/v1")))
OLLAMA_URL = EMBED_URL  # Backward compatibility alias
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION_NAME = "agent_episodic_memory_v2"
EMBEDDING_MODEL = "nomic-embed-text"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
EMBEDDING_DIM = 768  # Dimension for nomic-embed-text

def _get_embedding_dimension() -> int:
    try:
        resp = requests.post(f"{EMBED_URL}/embeddings", headers={"Authorization": "Bearer llama"}, json={
            "input": "test"
        }, timeout=30.0)
        if resp.status_code == 200:
            vec = resp.json().get("data", [{}])[0].get("embedding", [])
            return len(vec)
    except Exception:
        pass
    return 768

def _get_shard_name(category: str) -> str:
    safe_cat = "".join(c if c.isalnum() else "_" for c in category.lower())
    return f"agent_memory_{safe_cat}_v2"

_verified_shards = set()

def init_memory_qdrant(shard: str = "general") -> bool:
    """Ensure the Qdrant shard collection exists."""
    collection = _get_shard_name(shard)
    if collection in _verified_shards:
        return True
        
    try:
        resp = requests.get(f"{QDRANT_URL}/collections/{collection}", timeout=5.0)
        if resp.status_code == 404:
            dim = _get_embedding_dimension()
            create_resp = requests.put(f"{QDRANT_URL}/collections/{collection}", json={
                "vectors": {
                    "size": dim,
                    "distance": "Cosine"
                }
            }, timeout=10.0)
            if create_resp.status_code == 200:
                _verified_shards.add(collection)
                return True
            return False
            
        if resp.status_code == 200:
            _verified_shards.add(collection)
            return True
        return False
    except Exception as e:
        print(f"Failed to connect to Qdrant memory core for {collection}: {e}")
        return False

def get_embedding(text: str) -> Optional[List[float]]:
    # Truncate to ~1800 tokens to prevent embedding server batch size limits
    text = text[:7000]
    try:
        resp = requests.post(f"{EMBED_URL}/embeddings", headers={"Authorization": "Bearer llama"}, json={
            "input": text
        }, timeout=30.0)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            return data[0].get("embedding") if data else None
    except Exception as e:
        print(f"Error getting embedding: {e}")
    return None

RERANK_URL = "http://127.0.0.1:8082"
RERANK_MODEL = "qllama-bge-reranker-v2-m3-latest.gguf"

# Bound concurrent rerank HTTP calls. When analysis agents launch, semantic
# memory search can fire dozens of rerank requests at once; the BGE reranker is
# memory-bandwidth bound on the iGPU and the burst saturated DDR5 (caused the
# old 90/120s timeouts). Cap to a few at a time - the reranker batches documents
# per request, so throughput is preserved while bandwidth pressure drops.
_RERANK_SEM = __import__("threading").BoundedSemaphore(2)


def rerank_memories(query: str, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stage 2: Cross-Encoder Reranking via llama.cpp"""
    if not memories:
        return []
        
    reranked = []
    texts = [mem.get("payload", {}).get("fact", "") for mem in memories]
    
    try:
        with _RERANK_SEM:
            resp = requests.post(
                f"{RERANK_URL}/v1/rerank",
                headers={"Authorization": "Bearer llama"},
                json={
                    "model": RERANK_MODEL,
                    "query": query,
                    "documents": texts,
                    "top_n": 3
                },
                timeout=30.0
            )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            for res in results:
                idx = res.get("index")
                if idx is not None and idx < len(memories):
                    mem = memories[idx]
                    payload = mem.get("payload", {})
                    reranked.append({
                        "score": float(res.get("relevance_score", 0.0)),
                        "id": mem.get("id", ""),
                        "fact": payload.get("fact", ""),
                        "category": payload.get("category", "general")
                    })
            return reranked
    except Exception as e:
        print(f"Failed to call llama-server reranker on port 8082: {e}")
        
    # Fallback to Stage 1 Dense Retrieval scores if API fails
    for mem in memories:
        payload = mem.get("payload", {})
        reranked.append({
            "score": mem.get("score", 0.0),
            "id": mem.get("id", ""),
            "fact": payload.get("fact", ""),
            "category": payload.get("category", "general")
        })
        
    # Sort by score descending
    reranked.sort(key=lambda x: x["score"], reverse=True)
    return reranked[:3]  # Keep top 3

import time
import threading
import networkx as nx
import re

_kg_file = ".data/knowledge_graph.json"
_kg = None
# BUG FIX: KG is module-global and mutated/written from multiple threads via
# asyncio.to_thread(remember_fact, ...). Guard all read-modify-write with a lock
# to prevent corrupted/concurrent knowledge_graph.json writes.
_kg_lock = threading.Lock()

def _get_kg():
    global _kg
    with _kg_lock:
        if _kg is None:
            _kg = nx.DiGraph()
            if os.path.exists(_kg_file):
                try:
                    import json
                    with open(_kg_file, "r") as f:
                        data = json.load(f)
                        _kg = nx.node_link_graph(data)
                except Exception as e:
                    print(f"Error loading Knowledge Graph: {e}")
        return _kg

def _save_kg():
    with _kg_lock:
        if _kg is not None:
            os.makedirs(os.path.dirname(_kg_file), exist_ok=True)
            try:
                import json
                data = nx.node_link_data(_kg)
                with open(_kg_file, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                print(f"Error saving Knowledge Graph: {e}")

def _extract_relations(fact: str):
    """Simple heuristic relation extraction for the Knowledge Graph."""
    # Look for patterns like "X is a Y", "X requires Y", "X depends on Y"
    fact_lower = fact.lower()
    relations = []
    
    # Very basic regex heuristic for common agentic relations
    deps = re.findall(r'(\w+)\s+(?:depends on|requires|uses|calls)\s+(\w+)', fact_lower)
    for subj, obj in deps:
        relations.append((subj, "depends_on", obj))
        
    is_a = re.findall(r'(\w+)\s+(?:is a|is an)\s+(\w+)', fact_lower)
    for subj, obj in is_a:
        relations.append((subj, "is_a", obj))
        
    has_bug = re.findall(r'(\w+)\s+(?:has a bug|is broken|fails)', fact_lower)
    for subj in has_bug:
        relations.append((subj, "status", "broken"))
        
    return relations

def remember_fact(fact: str, category: str = "general") -> bool:
    """Agent tool to store a memory in a specific shard with temporal filtering and graph linkage."""
    if not init_memory_qdrant(category):
        return False
        
    vector = get_embedding(fact)
    if not vector:
        return False
        
    collection = _get_shard_name(category)
    point_id = str(uuid.uuid4())
    current_time = time.time()
    
    # 1. Store in Qdrant (Vector DB) with Timestamp
    try:
        resp = requests.put(f"{QDRANT_URL}/collections/{collection}/points", json={
            "points": [{
                "id": point_id,
                "vector": vector,
                "payload": {
                    "fact": fact,
                    "category": category,
                    "timestamp": current_time,
                    "valid_from": current_time,
                    "valid_until": None
                }
            }]
        }, timeout=10.0)
        success = resp.status_code in (200, 201)
    except Exception:
        success = False

    # 2. Extract and Store in Knowledge Graph (Hybrid Stack)
    if success:
        kg = _get_kg()
        relations = _extract_relations(fact)
        with _kg_lock:
            for subj, pred, obj in relations:
                kg.add_node(subj)
                kg.add_node(obj)
                kg.add_edge(subj, obj, relation=pred, timestamp=current_time)

            # Also just track keywords as basic nodes
            keywords = [w for w in fact.split() if len(w) > 5]
            for kw in keywords[:3]:
                kg.add_node(kw.lower(), timestamp=current_time)

        _save_kg()
        
    return success

def _moe_route_shards(query: str) -> List[str]:
    """ShardMemo: Route query to specific memory shards using MoE keyword gating."""
    q = query.lower()
    shards = set()
    if any(k in q for k in ["code", "script", "function", "bug", "error", "fix", "syntax"]):
        shards.add("code_snippets")
    if any(k in q for k in ["you should", "i like", "always", "never", "prefer", "my"]):
        shards.add("user_preferences")
    if any(k in q for k in ["rule", "system", "architecture", "pattern", "design"]):
        shards.add("system_rules")
    if any(k in q for k in ["past", "before", "trace", "reflection", "solution", "history"]):
        shards.add("self_reflection")

    # BUG FIX: Always include `general` as a base shard — most memories are stored
    # with category="general" but were unreachable when any keyword shard matched.
    shards.add("general")
    return list(shards)

def get_relevant_memories(query: str) -> str:
    """Called before stream_prompt to inject memory context using Hybrid MoE routing (Vector + KG)."""
    vector = get_embedding(query)
    if not vector:
        return ""
        
    active_shards = _moe_route_shards(query)
    all_results = []
    current_time = time.time()
    
    # --- 1. Vector DB Retrieval (with Temporal Decay) ---
    for shard in active_shards:
        if not init_memory_qdrant(shard):
            continue
            
        collection = _get_shard_name(shard)
        try:
            # Stage 1: Dense Retrieval on specific shard (Temporal Filter)
            resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/search", json={
                "vector": vector,
                "limit": 10,
                "with_payload": True,
                "score_threshold": 0.5,
                "filter": {
                    "must": [
                        {
                            "is_empty": {
                                "key": "valid_until"
                            }
                        }
                    ]
                }
            }, timeout=5.0)
            
            if resp.status_code == 200:
                for hit in resp.json().get("result", []):
                    payload = hit.get("payload", {})
                    # Temporal Filtering: decay score by age
                    # e.g., memory decays by 10% every 24 hours
                    age_seconds = current_time - payload.get("timestamp", current_time)
                    age_days = age_seconds / 86400.0
                    decay_factor = max(0.1, 1.0 - (age_days * 0.1))
                    
                    # Store temporally adjusted score
                    hit["score"] = hit.get("score", 0.0) * decay_factor
                    all_results.append(hit)
        except Exception:
            pass

    # --- 2. Knowledge Graph Retrieval ---
    kg_context = []
    kg = _get_kg()
    if kg:
        query_words = [w.lower() for w in query.split() if len(w) > 3]
        found_nodes = [n for n in kg.nodes() if isinstance(n, str) and any(w in n.lower() for w in query_words)]
        
        for node in found_nodes[:3]:
            # Get 1-hop neighborhood
            for neighbor in kg.successors(node):
                edge_data = kg.get_edge_data(node, neighbor)
                kg_context.append(f"{node} --[{edge_data.get('relation', 'related_to')}]--> {neighbor}")

    if not all_results and not kg_context:
        return ""
        
    # Stage 2: Rerank across all retrieved shards
    best_memories = rerank_memories(query, all_results)
    
    output = ["[EPISODIC MEMORY (Hybrid Stack)]"]
    if best_memories:
        output.append("Semantic Memories (Temporal Valid):")
        for m in best_memories:
            point_id = m.get("id", "")
            id_str = f" [ID: {point_id}]" if point_id else ""
            output.append(f"-{id_str} {m['fact']} (Shard: {m['category']})")
            
    if kg_context:
        output.append("Relational Knowledge Graph:")
        for rel in set(kg_context):
            output.append(f"- {rel}")
        
    return "\n".join(output)


def dump_all_failures(limit: int = 200) -> str:
    """Scroll ALL memories from the self_reflection shard without semantic filtering.
    
    This is used by the self-healing orchestrator to do full root-cause analysis.
    Unlike get_relevant_memories() which returns top-3 reranked hits, this returns
    a complete unfiltered dump of every failure the system has recorded, sorted
    by timestamp (newest first). The 9B model can then synthesize patterns.
    """
    collection = _get_shard_name("self_reflection")
    
    # First check the collection exists
    try:
        resp = requests.get(f"{QDRANT_URL}/collections/{collection}", timeout=5.0)
        if resp.status_code != 200:
            return "[No failure history found — self_reflection shard does not exist yet]"
        count = resp.json().get("result", {}).get("points_count", 0)
    except Exception as e:
        return f"[Failed to connect to Qdrant: {e}]"
    
    if count == 0:
        return "[Self-reflection shard is empty — no failures recorded yet]"
    
    # Scroll all points (no vector needed — payload only)
    all_facts = []
    offset = None
    fetched = 0
    
    while fetched < limit:
        body = {"limit": min(100, limit - fetched), "with_payload": True, "with_vector": False}
        if offset:
            body["offset"] = offset
        try:
            resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body, timeout=10.0)
            if resp.status_code != 200:
                break
            data = resp.json().get("result", {})
            points = data.get("points", [])
            if not points:
                break
            for pt in points:
                payload = pt.get("payload", {})
                fact = payload.get("fact", "")
                ts = payload.get("timestamp", 0)
                if fact:
                    all_facts.append((ts, fact))
            fetched += len(points)
            offset = data.get("next_page_offset")
            if not offset:
                break
        except Exception as e:
            break
    
    if not all_facts:
        return "[No failure records retrievable]"
    
    # Sort newest first
    all_facts.sort(key=lambda x: x[0], reverse=True)
    
    lines = [f"[FULL FAILURE HISTORY — {len(all_facts)} records from self_reflection shard (newest first)]"]
    for i, (ts, fact) in enumerate(all_facts, 1):
        lines.append(f"{i}. {fact}")
    
    return "\n".join(lines)


def get_failure_digest() -> dict:
    """Return a structured digest of failure statistics from all memory shards.
    
    Returns counts per shard and the most recent failures, for dashboard/reporting.
    """
    shards = ["self_reflection", "system_rules", "general", "errors", "code", "architecture"]
    digest = {"total": 0, "shards": {}}
    
    for shard in shards:
        collection = _get_shard_name(shard)
        try:
            resp = requests.get(f"{QDRANT_URL}/collections/{collection}", timeout=3.0)
            if resp.status_code == 200:
                count = resp.json().get("result", {}).get("points_count", 0)
                digest["shards"][shard] = count
                digest["total"] += count
        except Exception:
            digest["shards"][shard] = 0
    
    return digest


def deprecate_memory(point_id: str, category: str = "general") -> bool:
    """Agent tool to mark a memory as deprecated (fact staleness), keeping it for causality but removing it from active search."""
    collection = _get_shard_name(category)
    current_time = time.time()
    
    # We update the payload by setting valid_until
    try:
        # Use Qdrant's Set Payload API to merge the payload directly
        update_resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/payload", json={
            "payload": {"valid_until": current_time},
            "points": [point_id]
        }, timeout=10.0)
        return update_resp.status_code in (200, 201)
    except Exception as e:
        print(f"Failed to deprecate memory: {e}")
    return False


