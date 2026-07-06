import os
import ast
import json
import uuid
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

QDRANT_URL = "http://127.0.0.1:6333"
OLLAMA_URL = "http://127.0.0.1:11434"
COLLECTION_NAME = "codebase_index"
EMBEDDING_MODEL = "nomic-embed-text"

def get_embedding(text: str) -> Optional[List[float]]:
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings", json={
            "model": EMBEDDING_MODEL,
            "prompt": text
        }, timeout=30.0)
        if resp.status_code == 200:
            return resp.json().get("embedding")
    except Exception as e:
        print(f"Error getting embedding: {e}")
    return None

def init_qdrant() -> bool:
    """Ensure the Qdrant collection exists."""
    try:
        # Check if collection exists
        resp = requests.get(f"{QDRANT_URL}/collections/{COLLECTION_NAME}", timeout=5.0)
        if resp.status_code == 404:
            # Create collection (nomic-embed-text outputs 768 dims)
            create_resp = requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}", json={
                "vectors": {
                    "size": 768,
                    "distance": "Cosine"
                }
            }, timeout=10.0)
            return create_resp.status_code == 200
        return True
    except Exception as e:
        print(f"Failed to connect to Qdrant: {e}")
        return False

def extract_chunks(file_path: Path) -> List[Dict[str, str]]:
    """Extract semantic chunks from a python file."""
    chunks = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        
        # We will extract class bodies and function bodies
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                # Get the source segment if possible
                segment = ast.get_source_segment(content, node)
                if segment:
                    # Clean docstrings or just store the raw chunk
                    chunks.append({
                        "id": str(uuid.uuid4()),
                        "path": str(file_path),
                        "name": node.name,
                        "type": type(node).__name__,
                        "content": segment
                    })
    except Exception as e:
        # Fallback to plain file content if not python or unparseable
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if len(content.strip()) > 0:
            chunks.append({
                "id": str(uuid.uuid4()),
                "path": str(file_path),
                "name": file_path.name,
                "type": "File",
                "content": content[:4000] # Truncate to avoid massive chunks
            })
    return chunks

def index_codebase(root_dir: str) -> tuple[int, int]:
    """Index the codebase and return (files_processed, chunks_indexed)."""
    if not init_qdrant():
        raise RuntimeError("Failed to initialize Qdrant.")
        
    root_path = Path(root_dir)
    total_files = 0
    total_chunks = 0
    
    # First, clear existing points in the collection so we don't duplicate
    try:
        requests.delete(f"{QDRANT_URL}/collections/{COLLECTION_NAME}")
        init_qdrant()
    except Exception:
        pass

    all_points = []
    for py_file in root_path.rglob("*.py"):
        if ".venv" in py_file.parts or "node_modules" in py_file.parts or "__pycache__" in py_file.parts or ".git" in py_file.parts:
            continue
            
        total_files += 1
        chunks = extract_chunks(py_file)
        
        for chunk in chunks:
            # To get good embeddings, we embed a contextualized string
            context_str = f"File: {chunk['path']}\nType: {chunk['type']}\nName: {chunk['name']}\nCode:\n{chunk['content']}"
            vector = get_embedding(context_str)
            if vector:
                all_points.append({
                    "id": chunk["id"],
                    "vector": vector,
                    "payload": {
                        "path": str(Path(chunk["path"]).relative_to(root_path)),
                        "name": chunk["name"],
                        "type": chunk["type"],
                        "content": chunk["content"]
                    }
                })
                total_chunks += 1
                
                # Batch upsert every 100 points
                if len(all_points) >= 100:
                    requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points", json={
                        "points": all_points
                    }, timeout=30.0)
                    all_points = []
                    
    # Upsert any remaining points
    if all_points:
        requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points", json={
            "points": all_points
        }, timeout=30.0)
            
    return total_files, total_chunks
