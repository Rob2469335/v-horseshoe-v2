from __future__ import annotations

from pathlib import Path
from typing import Dict

class MCPRegistry:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[3]

    async def call(self, tool: str, params: Dict) -> Dict:
        if tool == "filesystem":
            return await self._filesystem(params)
        if tool == "qdrant_recall":
            return await self._qdrant_recall(params)
        return {"ok": False, "error": f"Unknown tool: {tool}"}

    async def _filesystem(self, params: Dict) -> Dict:
        try:
            requested_path = Path(params.get("path", ""))
            target_path = (self.root / requested_path).resolve()

            if not str(target_path).startswith(str(self.root)):
                return {"ok": False, "error": "Access denied: Path outside sandbox", "path": str(target_path)}

            if not target_path.exists():
                return {"ok": False, "error": "File not found", "path": str(target_path)}

            if not target_path.is_file():
                return {"ok": False, "error": "Path is not a file", "path": str(target_path)}

            content = target_path.read_text(encoding="utf-8")
            return {"ok": True, "content": content, "path": str(target_path)}
        except Exception as e:
            return {"ok": False, "error": str(e), "path": str(params.get("path", ""))}

    async def _qdrant_recall(self, params: Dict) -> Dict:
        try:
            from swarm_os.lib.vector.qdrant_store import search

            query = str(params.get("query", "")).strip()
            collection = str(params.get("collection", "chat_archive")).strip() or "chat_archive"

            if not query:
                return {"ok": False, "error": "Missing required parameter: query", "results": []}

            results = await search(collection, query, top_k=5)
            return {"ok": True, "results": results}
        except Exception as e:
            return {"ok": False, "error": str(e), "results": []}

registry = MCPRegistry()
