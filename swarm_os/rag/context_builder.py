from collections import defaultdict
from typing import List, Dict, Any
import os

class ContextBuilder:
    def __init__(self, repo_root: str, max_files: int = 25):
        self.repo_root = repo_root
        self.max_files = max_files

    async def build(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        files = self._group_by_file(chunks)
        expanded = await self._expand_files(files)
        expanded = await self._expand_related(expanded)
        return self._pack(expanded)

    def _group_by_file(self, chunks):
        grouped = defaultdict(list)
        for c in chunks:
            payload = c.get("payload") or {} if isinstance(c, dict) else {}
            file_path = (
                c.get("file_path") or c.get("source") or c.get("file") or
                payload.get("file_path") or payload.get("source") or payload.get("file")
            )
            if file_path:
                file_path = str(file_path).replace("C:\\Users\\rober\\Projects\\v-horseshoe-v2\\", "")
                grouped[file_path].append(c)
        return grouped

    async def _expand_files(self, grouped):
        expanded = []

        for file_path, chunks in grouped.items():
            full_path = os.path.join(self.repo_root, file_path)

            if not os.path.exists(full_path):
                continue

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()

                expanded.append({
                    "file_path": file_path,
                    "content": content,
                    "matched_chunks": chunks
                })
            except FileNotFoundError:
                # It's perfectly fine if the file simply isn't there
                pass
            except Exception as e:
                # If something else goes wrong, let's print a warning so we know!
                print(f"Warning: Could not read {file_path}: {e}")

        return expanded

    async def _expand_related(self, files):
        seen = set(f["file_path"] for f in files)
        extra = []

        for f in files:
            for imp in self._extract_imports(f["content"]):
                if imp in seen:
                    continue

                path = self._resolve_import_to_path(imp)
                if path and os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f2:
                            extra.append({
                                "file_path": imp,
                                "content": f2.read(),
                                "matched_chunks": []
                            })
                        seen.add(imp)
                    except FileNotFoundError:
                        pass
                    except Exception as e:
                        print(f"Warning: Could not read imported file {imp}: {e}")

        return files + extra

    def _pack(self, files):
        out = []
        for f in files[:self.max_files]:
            out.append({
                "role": "system",
                "content": f"\n\n# FILE: {f['file_path']}\n\n{f['content']}\n"
            })
        return out

    def _extract_imports(self, code: str):
        imports = []
        for line in code.splitlines():
            line = line.strip()
            if line.startswith("import "):
                imports.append(line.replace("import ", "").split(" ")[0])
            if line.startswith("from "):
                parts = line.split(" ")
                if len(parts) > 1:
                    imports.append(parts[1])
        return imports

    def _resolve_import_to_path(self, module: str):
        return os.path.join(self.repo_root, module.replace(".", os.sep) + ".py")
