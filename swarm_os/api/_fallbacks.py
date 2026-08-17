from pathlib import Path
from typing import List, Dict, Any

# Safe local-file fallback utilities used by /features/search when vector
# search is degraded. This helper restricts which files can be scanned to
# documentation locations only to avoid exposing config or secret files.

ALLOWED_ROOT_NAMES = {"AGENTS.md", "README.md", "docs"}
DISALLOWED_DIRS = {"config", "models", "data", "logs", "tests"}


def local_docs_search(
    repo_root: Path, tokens: set[str], top_k: int = 5
) -> List[Dict[str, Any]]:
    """Scan a small, safe set of repository documentation files for keyword
    matches and return scored results suitable for the degraded lexical
    fallback.

    - Only files named AGENTS.md, README.md and files under docs/ are considered.
    - Disallowed directories (config, models, data, logs, tests) are never read.
    - File reads are defensive and any IO error yields skip for that file.
    """
    results = []

    try:
        candidates = []
        # Always consider AGENTS.md and README.md if present
        for name in ("AGENTS.md", "README.md"):
            p = repo_root / name
            if p.exists() and p.is_file():
                candidates.append(p)

        docs_dir = repo_root / "docs"
        if docs_dir.exists() and docs_dir.is_dir():
            for p in sorted(docs_dir.rglob("*.md")):
                # Skip files inside disallowed dirs
                if any(part in DISALLOWED_DIRS for part in p.parts):
                    continue
                candidates.append(p)

        # If no candidates found yet, broaden to all top-level markdown files
        if not candidates:
            for p in sorted(repo_root.glob("*.md")):
                if any(part in DISALLOWED_DIRS for part in p.parts):
                    continue
                candidates.append(p)

        file_results = []
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            hay = text.lower()
            score = sum(1 for t in tokens if t in hay)
            if score:
                # build a short excerpt around first token match
                first_token = next((t for t in tokens if t in hay), None)
                excerpt = ""
                if first_token:
                    idx = hay.find(first_token)
                    if idx >= 0:
                        raw = text
                        start = max(0, idx - 100)
                        excerpt = raw[start : start + 300]
                file_results.append(
                    (
                        score,
                        {
                            "id": str(path),
                            "score": float(score),
                            "payload": {"path": str(path), "excerpt": excerpt},
                        },
                    )
                )

        file_results.sort(key=lambda x: -x[0])
        results = [item for _, item in file_results[:top_k]]
    except Exception:
        return []

    return results
