import os
import ast
import json
import uuid
import warnings
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

QDRANT_URL = "http://127.0.0.1:6333"
LLAMA_URL = "http://127.0.0.1:8081"
COLLECTION_NAME = "codebase_index"
EMBEDDING_MODEL = "nomic-embed-text-v1.5.Q8_0.gguf"

def get_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    # Truncate to prevent context window overflows (stay under 2048 token batch limit)
    texts = [text[:4000] for text in texts]
    try:
        resp = requests.post(f"{LLAMA_URL}/v1/embeddings", headers={"Authorization": "Bearer llama"}, json={
            "model": EMBEDDING_MODEL,
            "input": texts
        }, timeout=300.0)
        if resp.status_code != 200:
            print(f"Server returned {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [item.get("embedding") for item in data] if data else None
    except Exception as e:
        print(f"Error getting embeddings: {e}")
        return None

def get_embedding(text: str) -> Optional[List[float]]:
    """Helper for single string embedding."""
    embeddings = get_embeddings([text])
    return embeddings[0] if embeddings else None

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

def read_text_auto(file_path: Path) -> str:
    """Reads a file's text, automatically detecting UTF-16 (LE/BE BOM or null-byte heuristic) and UTF-8."""
    try:
        raw = file_path.read_bytes()
    except Exception:
        return ""
    if not raw:
        return ""
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        try:
            return raw.decode("utf-16", errors="replace").replace('\u0000', '')
        except Exception:
            pass
    if raw.startswith(b'\xef\xbb\xbf'):
        try:
            return raw.decode("utf-8-sig", errors="replace").replace('\u0000', '')
        except Exception:
            pass
    sample = raw[:1000]
    if sample.count(b'\x00') > len(sample) * 0.15:
        try:
            return raw.decode("utf-16-le", errors="replace").replace('\u0000', '')
        except Exception:
            try:
                return raw.decode("utf-16-be", errors="replace").replace('\u0000', '')
            except Exception:
                pass
    return raw.decode("utf-8", errors="replace").replace('\u0000', '')

def extract_chunks(file_path: Path) -> List[Dict[str, str]]:
    """Extract semantic chunks from a python file."""
    chunks = []
    try:
        content = read_text_auto(file_path)
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
                            "content": segment.replace('\u0000', '')
                        })
            if chunks:
                return chunks
        raise ValueError("Not parsed as Python")
    except Exception as e:
        # Fallback to plain file content if not python or unparseable
        content = read_text_auto(file_path)
        if len(content.strip()) > 0:
            chunk_size = 3500
            for i in range(0, len(content), chunk_size):
                chunks.append({
                    "id": str(uuid.uuid4()),
                    "path": str(file_path),
                    "name": f"{file_path.name}_part_{i//chunk_size + 1}",
                    "type": "File",
                    "content": content[i:i+chunk_size]
                })
    return chunks

def index_codebase(root_dir: str, clear: bool = True) -> tuple[int, int]:
    """Index the codebase and return (files_processed, chunks_indexed)."""
    if not init_qdrant():
        raise RuntimeError("Failed to initialize Qdrant.")
        
    root_path = Path(root_dir)
    total_files = 0
    total_chunks = 0
    
    # First, clear existing points in the collection so we don't duplicate
    if clear:
        try:
            requests.delete(f"{QDRANT_URL}/collections/{COLLECTION_NAME}")
            init_qdrant()
        except Exception:
            pass

    all_points = []
    current_batch_chunks = []
    batch_size = 16

    def flush_batch():
        nonlocal total_chunks, all_points, current_batch_chunks
        if not current_batch_chunks:
            return
            
        texts = [
            f"File: {c['path']}\nType: {c['type']}\nName: {c['name']}\nCode:\n{c['content']}"
            for c in current_batch_chunks
        ]
        
        vectors = get_embeddings(texts)
        if vectors and len(vectors) == len(current_batch_chunks):
            for chunk, vector in zip(current_batch_chunks, vectors):
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
                    
        current_batch_chunks = []
        import time
        time.sleep(0.05)

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
            current_batch_chunks.append(chunk)
            if len(current_batch_chunks) >= batch_size:
                flush_batch()
                
    flush_batch()
                    
    # Upsert any remaining points
    if all_points:
        resp = requests.put(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points", json={
            "points": all_points
        }, timeout=120.0)
        resp.raise_for_status()
            
    return total_files, total_chunks

