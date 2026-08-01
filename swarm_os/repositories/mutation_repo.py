import json
import shutil
from pathlib import Path
from typing import Dict, Any

class MutationRepository:
    def __init__(self, root_dir: str | Path = ".data/pending_mutations"):
        self.root_dir = Path(root_dir)

    def get_mutation_path(self, mutation_id: str) -> Path:
        return self.root_dir / mutation_id
        
    def approve(self, mutation_id: str) -> Dict[str, Any]:
        target_dir = self.get_mutation_path(mutation_id)
        meta_path = target_dir / "metadata.json"
        
        if not meta_path.exists():
            raise FileNotFoundError(f"Mutation {mutation_id} not found")

        metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        pending_file = Path(metadata["pending_file"])
        target_path = Path(metadata["target_path"])

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
