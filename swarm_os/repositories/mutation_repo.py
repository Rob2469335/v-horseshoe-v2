import json
import shutil
from pathlib import Path
from typing import Dict, Any

class MutationRepository:
    def __init__(self, root_dir: str | Path = ".data/pending_mutations"):
        self.root_dir = Path(root_dir)

    def get_mutation_path(self, mutation_id: str) -> Path:
        return self.root_dir / mutation_id

    def _resolve_within_root(self, raw: str, *, allow_project_root: bool) -> Path:
        """Resolve a path from mutation metadata and enforce it stays inside the
        project tree.

        `pending_file` (staged in .data/pending_mutations/<id>/) and
        `target_path` (the live file the mutation replaces) both come from a
        metadata.json that a crafted mutation could poison with an absolute or
        `..`-escaping path, turning `approve()` into an arbitrary file write.
        Reject any path that resolves outside the allowed base.
        """
        resolved = Path(raw).expanduser().resolve()
        base = (self.root_dir if not allow_project_root else self.root_dir.parents[1]).resolve()
        if not (resolved == base or base in resolved.parents):
            raise ValueError(f"Path outside allowed root: {raw}")
        return resolved

    def approve(self, mutation_id: str) -> Dict[str, Any]:
        target_dir = self.get_mutation_path(mutation_id)
        meta_path = target_dir / "metadata.json"
        
        if not meta_path.exists():
            raise FileNotFoundError(f"Mutation {mutation_id} not found")

        metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        # pending_file lives inside the mutation's own staging dir.
        pending_file = self._resolve_within_root(metadata["pending_file"], allow_project_root=False)
        # target_path is the live project file being replaced — allow it anywhere
        # under the project root, but never outside it.
        target_path = self._resolve_within_root(metadata["target_path"], allow_project_root=True)

        if not pending_file.exists():
            raise FileNotFoundError(f"Pending mutation file not found: {pending_file}")

        target_path.parent.mkdir(parents=True, exist_ok=True)

        backup_path = None
        if target_path.exists():
            backup_path = target_path.with_suffix(target_path.suffix + ".bak")
            shutil.copy2(target_path, backup_path)

        shutil.copy2(pending_file, target_path)

        metadata["approved"] = True
        if backup_path:
            metadata["backup_path"] = str(backup_path)

        meta_path.write_text(json.dumps(metadata, indent=2))
        return metadata
        
    def reject(self, mutation_id: str) -> bool:
        target_dir = self.get_mutation_path(mutation_id)
        if not target_dir.exists():
            return False
        shutil.rmtree(target_dir)
        return True
    def list_pending(self) -> list[Dict[str, Any]]:
        mutations = []
        if not self.root_dir.exists():
            return mutations
        for meta_file in self.root_dir.glob("*/metadata.json"):
            try:
                mutations.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except Exception:
                continue
        return mutations
