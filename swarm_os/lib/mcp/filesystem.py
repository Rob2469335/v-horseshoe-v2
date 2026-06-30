from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

async def filesystem_handler(params: Dict[str, Any], root: Path, trace_hook=None) -> Dict[str, Any]:
    """
    Handles filesystem operations within a sandboxed root.
    """
    operation = params.get("operation", "read")
    requested = str(params.get("path", ""))
    
    # Resolve and force to be absolute paths
    root = root.resolve()
    
    def resolve_in_sandbox(requested_path_str: str) -> Path:
        # Strip leading slashes so Path() doesn't treat it as absolute root
        requested_path_str = requested_path_str.lstrip("\\/")
        requested_path = Path(requested_path_str or ".")
        target_path = (root / requested_path).resolve()
        try:
            target_path.relative_to(root)
        except ValueError as e:
            raise ValueError(f"Path is outside sandbox: {requested_path_str}") from e
        return target_path

    try:
        target_path = resolve_in_sandbox(requested)

        if operation == "read":
            if not target_path.exists():
                return {"ok": False, "error": f"File not found: {requested}", "path": str(target_path)}
            if not target_path.is_file():
                return {"ok": False, "error": f"Not a file: {requested}", "path": str(target_path)}

            content = target_path.read_text(encoding="utf-8", errors="replace")
            if trace_hook:
                trace_hook("filesystem_read", {"ok": True, "path": str(target_path)})
            return {"ok": True, "content": content}
        
        elif operation == "write":
            content = str(params.get("content", ""))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            if trace_hook:
                trace_hook("filesystem_write", {"ok": True, "path": str(target_path)})
            return {"ok": True}

        elif operation == "patch":
            if not target_path.exists():
                return {"ok": False, "error": f"File not found for patching: {requested}"}
            
            old_str = str(params.get("old", params.get("old_string", "")))
            new_str = str(params.get("new", params.get("new_string", "")))
            
            content = target_path.read_text(encoding="utf-8")
            if old_str not in content:
                return {"ok": False, "error": "Surgical Error: 'old' not found in file content."}
            
            # Precise single-occurrence check for safety
            if content.count(old_str) > 1 and not params.get("allow_multiple", False):
                return {"ok": False, "error": "Surgical Ambiguity: 'old' occurs multiple times. Provide more context."}
            
            updated_content = content.replace(old_str, new_str)
            target_path.write_text(updated_content, encoding="utf-8")
            
            return {
                "ok": True, 
                "replaced": 1 if not params.get("allow_multiple") else content.count(old_str)
            }

        elif operation == "list":
            if not target_path.exists():
                return {"ok": False, "error": f"Directory not found: {requested}", "path": str(target_path)}
            if not target_path.is_dir():
                return {"ok": False, "error": f"Not a directory: {requested}", "path": str(target_path)}
            
            entries = []
            for item in target_path.iterdir():
                entries.append({
                    "name": item.name,
                    "size": item.stat().st_size if item.is_file() else 0
                })
            return {"ok": True, "entries": entries}

        elif operation == "grep":
            pattern = str(params.get("pattern", ""))
            recursive = bool(params.get("recursive", True))
            matches = []
            
            def search_file(file_path: Path):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    for i, line in enumerate(content.splitlines(), 1):
                        if pattern in line:
                            rel_file = str(file_path.relative_to(root)).replace("\\", "/")
                            matches.append({
                                "file": rel_file,
                                "line": i,
                                "text": line
                            })
                except Exception:
                    pass

            if target_path.is_file():
                search_file(target_path)
            elif target_path.is_dir():
                if recursive:
                    for p in target_path.rglob("*"):
                        if p.is_file() and not any(part.startswith('.') or part in ('node_modules', '.venv') for part in p.parts):
                            search_file(p)
                else:
                    for p in target_path.glob("*"):
                        if p.is_file():
                            search_file(p)
            
            return {"ok": True, "matches": matches}

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        logger.exception("Filesystem tool error")
        return {"ok": False, "error": str(e), "path": requested}
