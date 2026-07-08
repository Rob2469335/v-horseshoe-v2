import os
import json
import uuid
import requests
from typing import List, Dict, Any, Optional

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION_NAME = "agent_episodic_memory_v2"
EMBEDDING_MODEL = "nomic-embed-text"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
EMBEDDING_DIM = 768  # Dimension for nomic-embed-text

def _get_embedding_dimension() -> int:
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings", json={
            "model": EMBEDDING_MODEL,
            "prompt": "test",
            "keep_alive": "5m"
        }, timeout=30.0)
        if resp.status_code == 200:
            vec = resp.json().get("embedding", [])
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
            
        _verified_shards.add(collection)
        return True
    except Exception as e:
        print(f"Failed to connect to Qdrant memory core for {collection}: {e}")
        return False

def get_embedding(text: str) -> Optional[List[float]]:
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings", json={
            "model": EMBEDDING_MODEL,
            "prompt": text,
            "keep_alive": "5m"
        }, timeout=30.0)
        if resp.status_code == 200:
            return resp.json().get("embedding")
    except Exception as e:
        print(f"Error getting embedding: {e}")
    return None

_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            import logging
            logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
            # Load the actual HuggingFace PyTorch model natively
            print("Loading BAAI/bge-reranker-v2-m3 into PyTorch...")
            _reranker = CrossEncoder('BAAI/bge-reranker-v2-m3')
        except Exception as e:
            print(f"Failed to load CrossEncoder: {e}")
            _reranker = False
    return _reranker if _reranker is not False else None

def rerank_memories(query: str, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stage 2: Cross-Encoder Reranking"""
    if not memories:
        return []
    
    reranker = get_reranker()
    
    reranked = []
    
    if reranker:
        # High-Precision Cross-Encoder Scoring
        pairs = [[query, mem.get("payload", {}).get("fact", "")] for mem in memories]
        scores = reranker.predict(pairs)
        for i, mem in enumerate(memories):
            payload = mem.get("payload", {})
            reranked.append({
                "score": float(scores[i]),
                "fact": payload.get("fact", ""),
                "category": payload.get("category", "general")
            })
    else:
        # Fallback to Stage 1 Dense Retrieval scores if PyTorch fails
        for mem in memories:
            payload = mem.get("payload", {})
            reranked.append({
                "score": mem.get("score", 0.0),
                "fact": payload.get("fact", ""),
                "category": payload.get("category", "general")
            })
        
    # Sort by score descending
    reranked.sort(key=lambda x: x["score"], reverse=True)
    return reranked[:3]  # Keep top 3

def remember_fact(fact: str, category: str = "general") -> bool:
    """Agent tool to store a memory in a specific shard."""
    if not init_memory_qdrant(category):
        return False
        
    vector = get_embedding(fact)
    if not vector:
        return False
        
    collection = _get_shard_name(category)
    point_id = str(uuid.uuid4())
    try:
        resp = requests.put(f"{QDRANT_URL}/collections/{collection}/points", json={
            "points": [{
                "id": point_id,
                "vector": vector,
                "payload": {
                    "fact": fact,
                    "category": category
                }
            }]
        }, timeout=10.0)
        return resp.status_code in (200, 201)
    except Exception:
        return False

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
    
    if not shards:
        shards.add("general")
    return list(shards)

def get_relevant_memories(query: str) -> str:
    """Called before stream_prompt to inject memory context using MoE routing."""
    vector = get_embedding(query)
    if not vector:
        return ""
        
    active_shards = _moe_route_shards(query)
    all_results = []
    
    for shard in active_shards:
        if not init_memory_qdrant(shard):
            continue
            
        collection = _get_shard_name(shard)
        try:
            # Stage 1: Dense Retrieval on specific shard
            resp = requests.post(f"{QDRANT_URL}/collections/{collection}/points/search", json={
                "vector": vector,
                "limit": 10,
                "with_payload": True,
                "score_threshold": 0.5
            }, timeout=5.0)
            
            if resp.status_code == 200:
                all_results.extend(resp.json().get("result", []))
        except Exception:
            pass

    if not all_results:
        return ""
        
    # Stage 2: Rerank across all retrieved shards
    best_memories = rerank_memories(query, all_results)
    
    if not best_memories:
        return ""
        
    output = ["[EPISODIC MEMORY (MoE Shard-Routed)]"]
    for m in best_memories:
        output.append(f"- {m['fact']} (Shard: {m['category']})")
        
    return "\n".join(output)
