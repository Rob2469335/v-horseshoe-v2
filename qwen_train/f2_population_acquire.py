"""Pin, acquire, validate and normalise an immutable SWE-bench-Live snapshot (F2 R1/R8).

Why this module exists
----------------------
The F2 population was screened from ``data/f2_population/raw/swe_bench_live_full.jsonl``,
a *projection* of unknown provenance whose rows lack ``problem_statement`` and the
gold ``patch``.  Nothing in the repository could (a) name the upstream revision it
came from, or (b) re-acquire it.  This module makes acquisition of the population
source reproducible: it pins a full-length commit SHA, downloads only that
revision's parquet shards, verifies them, and writes a normalised JSONL that
preserves **every** upstream field.

Authority
---------
F2-IMPL-AUTH-029 (operator, 2026-10-09) authorises selecting a dataset, acquiring
and preserving a complete reproducible snapshot, pinning the immutable revision,
and generating reproducible manifests/censuses.

Network use
-----------
This is the *only* module in the F2 pipeline that fetches bytes.  It is run by an
operator/agent, never during screening or arm execution.  Screening
(:mod:`qwen_train.f2_population`) stays pure and offline.

Design rules
------------
* **The revision is a full-length commit SHA.**  A branch name is refused -- a
  branch is not immutable.
* **Raw artifacts are preserved byte-for-byte** next to the normalised JSONL, so
  the acquisition can be re-verified without re-downloading.
* **Nothing is silently dropped.**  The normaliser copies all upstream columns.
* Dates are kept as the upstream ``timestamp[ns]`` rendered to ISO-8601 UTC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "f2_population_source_v1"

#: The candidate sources evaluated in the F2 population recovery (2026-10-09).
#: Only full-length 40-hex commit SHAs are accepted.
SOURCES: dict[str, dict[str, str]] = {
    "swe-bench-live": {
        "repo_id": "SWE-bench-Live/SWE-bench-Live",
        "revision": "b51a86422e10cfd403beb4773e5a2947953e36ec",
        "license": "mit",
        "split": "full",
        "allow_patterns": "data/full-*.parquet",
    },
    "swe-rebench-v2": {
        "repo_id": "nebius/SWE-rebench-V2",
        "revision": "10483de0f50fe5da545942705a76c6150171af7f",
        "license": "cc-by-4.0",
        "split": "train",
        "allow_patterns": "*",
    },
}

_HEX40 = set("0123456789abcdef")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_full_sha(value: str) -> bool:
    v = (value or "").strip().lower()
    return len(v) == 40 and set(v) <= _HEX40


def normalise_cell(value: Any) -> Any:
    """Render a parquet cell to a JSON-safe value without dropping information.

    Timestamps become ISO-8601 UTC strings (``YYYY-MM-DDTHH:MM:SS``); lists/tuples
    stay lists; bytes are decoded.  ``None`` stays ``None`` -- missing evidence is
    never turned into an empty string here.
    """
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [normalise_cell(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): normalise_cell(v) for k, v in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        text = iso()
        # pandas/pyarrow render tz-naive UTC as ...T...; strip a trailing 'Z' or '+00:00'.
        return text.replace("+00:00", "").replace("Z", "")
    return str(value)


def acquire(
    source_key: str,
    dest_dir: Path,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Download the pinned revision's shards into ``dest_dir`` and return provenance.

    Refuses a non-immutable revision.  Never overwrites an existing normalised
    JSONL -- callers choose a fresh destination.
    """
    if source_key not in SOURCES:
        raise ValueError(f"unknown source {source_key!r}; known: {sorted(SOURCES)}")
    src = SOURCES[source_key]
    rev = src["revision"]
    if not _is_full_sha(rev):
        raise ValueError(f"revision is not a full 40-hex commit SHA: {rev!r}")

    from huggingface_hub import HfApi, snapshot_download  # imported lazily

    api = HfApi(token=token)
    info = api.dataset_info(src["repo_id"], revision=rev, token=token)
    resolved = getattr(info, "sha", None)
    if resolved != rev:
        raise RuntimeError(
            f"pinned revision {rev} did not resolve to itself (got {resolved!r})"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = dest_dir / "raw"
    snapshot_download(
        repo_id=src["repo_id"],
        repo_type="dataset",
        revision=rev,
        allow_patterns=[src["allow_patterns"]],
        local_dir=str(dest_dir / "_snapshot"),
        token=token,
    )

    # Collect the acquired shards, preserving their bytes under raw/.
    shards = sorted((dest_dir / "_snapshot").rglob("*.parquet"))
    if not shards:
        raise RuntimeError("no parquet shards were downloaded")
    raw_dir.mkdir(parents=True, exist_ok=True)
    shard_records = []
    for shard in shards:
        target = raw_dir / shard.name
        data = shard.read_bytes()
        target.write_bytes(data)
        shard_records.append(
            {"name": shard.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )

    # Normalise to one JSONL row per task, preserving every column.
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    for rec in shard_records:
        table = pq.read_table(raw_dir / rec["name"])
        if not columns:
            columns = list(table.column_names)
        elif list(table.column_names) != columns:
            raise RuntimeError("shards disagree on schema")
        for batch in table.to_batches():
            for row in batch.to_pylist():
                rows.append({k: normalise_cell(v) for k, v in row.items()})

    out_jsonl = dest_dir / "acquired.jsonl"
    if out_jsonl.exists():
        raise RuntimeError(f"refusing to overwrite existing {out_jsonl}")
    with out_jsonl.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
            fh.write("\n")

    ids = [str(r.get("instance_id") or "") for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    provenance = {
        "schema": SCHEMA,
        "source_key": source_key,
        "repo_id": src["repo_id"],
        "revision": rev,
        "split": src["split"],
        "license": src["license"],
        "acquisition_tool": "huggingface_hub.snapshot_download",
        "acquisition_tool_version": _hf_version(),
        "downloaded_at_utc": _utc_now(),
        "shards": shard_records,
        "columns": columns,
        "row_count": len(rows),
        "distinct_instance_ids": len(set(ids)),
        "duplicate_instance_ids": dup,
        "normalised_jsonl": "acquired.jsonl",
        "normalised_sha256": _sha256_file(out_jsonl),
    }
    (dest_dir / "PROVENANCE.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return provenance


def _hf_version() -> str:
    try:
        import huggingface_hub

        return getattr(huggingface_hub, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_acquired(path: Path) -> list[dict[str, Any]]:
    """Read a normalised ``acquired.jsonl``.  Pure; no network."""
    out = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, choices=sorted(SOURCES))
    ap.add_argument("--dest", required=True, type=Path)
    args = ap.parse_args(argv)
    prov = acquire(args.source, args.dest)
    print(json.dumps({k: prov[k] for k in (
        "repo_id", "revision", "row_count", "distinct_instance_ids",
        "duplicate_instance_ids", "normalised_sha256")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
