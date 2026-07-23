import os
import ast
import json
import uuid
import warnings
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

QDRANT_URL = "http://127.0.0.1:6333"
OLLAMA_URL = "http://127.0.0.1:11434"
COLLECTION_NAME = "codebase_index"
EMBEDDING_MODEL = "nomic-embed-text"

def get_embedding(text: str) -> Optional[List[float]]:
    # Truncate to ~1800 tokens to prevent Ollama batch size limits (2048 tokens max)
    text = text[:7000]
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/embeddings", json={
            "model": EMBEDDING_MODEL,
            "prompt": text
        }, timeout=120.0)
        resp.raise_for_status()
        return resp.json().get("embedding")
    except Exception as e:
        print(f"Error getting embedding: {e}")
        raise RuntimeError(f"Failed to generate embedding from Ollama: {e}")

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
            create_resp.raise_for_status()
            return True
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Failed to connect to Qdrant: {e}")
        return False

def extract_chunks(file_path: Path) -> List[Dict[str, str]]:
    """Extract semantic chunks from a python file."""
    chunks = []
    try:
        content = file_path.read_text(encoding="utf-8")
        if file_path.suffix.lower() == '.py':
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
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
            if chunks:
                return chunks
        raise ValueError("Not parsed as Python")
    except Exception as e:
        # Fallback to plain file content if not python or unparseable
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if len(content.strip()) > 0:
            chunk_size = 6500
            for i in range(0, len(content), chunk_size):
                chunks.append({
                    "id": str(uuid.uuid4()),
                    "path": str(file_path),
                    "name": f"{file_path.name}_part_{i//chunk_size + 1}",
                    "type": "File",
                    "content": content[i:i+chunk_size]
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
    ALLOWED_EXTS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.md', '.txt', '.html', '.css', '.json', '.yaml', '.yml', '.rs', '.jsonl', '.csv'}
    for py_file in root_path.rglob("*"):
        if not py_file.is_file() or py_file.suffix.lower() not in ALLOWED_EXTS:
            continue
        # Ignore common hidden/temporary directories to prevent indexing sandboxes or logs
        if any(part.startswith(".sandbox") or part.startswith(".gemini") or part in [".venv", "node_modules", "__pycache__", ".git", "build", "dist"] for part in py_file.parts):
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
                    resp = requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points", json={
                        "points": all_points
                    }, timeout=120.0)
                    resp.raise_for_status()
                    all_points = []
                    
    # Upsert any remaining points
    if all_points:
        resp = requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points", json={
            "points": all_points
        }, timeout=120.0)
        resp.raise_for_status()
            
    return total_files, total_chunks
