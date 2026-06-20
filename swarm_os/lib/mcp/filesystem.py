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
    
    def resolve_in_sandbox(requested_path_str: str) -> Path:
        requested_path = Path(requested_path_str or "")
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
            return {"ok": True, "path": str(target_path), "content": content}
        
        elif operation == "write":
            content = str(params.get("content", ""))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            if trace_hook:
                trace_hook("filesystem_write", {"ok": True, "path": str(target_path)})
            return {"ok": True, "path": str(target_path), "message": "File written successfully"}

        elif operation == "patch":
            # SINGULARITY: Surgical Patching (Replacer)
            if not target_path.exists():
                return {"ok": False, "error": f"File not found for patching: {requested}"}
            
            old_str = str(params.get("old_string", ""))
            new_str = str(params.get("new_string", ""))
            
            content = target_path.read_text(encoding="utf-8")
            if old_str not in content:
                return {"ok": False, "error": "Surgical Error: 'old_string' not found in file content."}
            
            # Precise single-occurrence check for safety
            if content.count(old_str) > 1 and not params.get("allow_multiple", False):
                return {"ok": False, "error": "Surgical Ambiguity: 'old_string' occurs multiple times. Provide more context."}
            
            updated_content = content.replace(old_str, new_str)
            target_path.write_text(updated_content, encoding="utf-8")
            
            return {
                "ok": True, 
                "path": str(target_path), 
                "message": "Surgical patch applied successfully.",
                "changes": 1 if not params.get("allow_multiple") else content.count(old_str)
            }

        elif operation == "list":
            if not target_path.exists():
                return {"ok": False, "error": f"Directory not found: {requested}", "path": str(target_path)}
            if not target_path.is_dir():
                return {"ok": False, "error": f"Not a directory: {requested}", "path": str(target_path)}
            
            items = []
            for item in target_path.iterdir():
                items.append({
                    "name": item.name,
                    "type": "file" if item.is_file() else "dir",
                    "size": item.stat().st_size if item.is_file() else 0
                })
            return {"ok": True, "path": str(target_path), "items": items}

        return {"ok": False, "error": f"Unknown operation: {operation}"}

    except Exception as e:
        logger.exception("Filesystem tool error")
        return {"ok": False, "error": str(e), "path": requested}
