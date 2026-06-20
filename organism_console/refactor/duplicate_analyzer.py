from pathlib import Path
import ast

class DuplicateAnalyzer:
    def __init__(self, root: str):
        self.root = Path(root)
        self.swarm = self.root / "swarm_os"
        self.console = self.root / "organism_console"

    def scan(self):
        return {
            "orchestrators": self._find("orchestrator"),
            "competition": self._find("competition"),
            "genomes": self._find("genome"),
            "memory": self._find("memory"),
            "runtime": self._find("runtime"),
        }

    def _find(self, keyword: str):
        results = []
        for base in [self.swarm, self.console]:
            if not base.exists():
                continue
            for file in base.rglob("*.py"):
                if keyword in file.name.lower():
                    results.append(str(file))
        return results

    def build_conflict_map(self):
        scan = self.scan()
        conflicts = {}

        for k, paths in scan.items():
            if len(paths) > 1:
                conflicts[k] = {
                    "all_locations": paths,
                    "swarm_os": [p for p in paths if "swarm_os" in p]
                }

        return conflicts
