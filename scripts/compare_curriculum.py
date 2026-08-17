"""Compare the PARTIAL-data curriculum (Version A, frozen at 322 items) against
the FULL-data curriculum (Version B, rebuilt from all 842 games).

Run ONLY after the analysis job reaches 842/842 — this is the A/B comparison
that tells us whether the original 322-item coach was identifying stable
weaknesses or overfitting to the first portion of the games.

The frozen Version A reference lives at data/chess/baseline/curriculum_A_322.jsonl.
Version B is generated fresh from the COMPLETE mistake store.

Metrics reported:
  original_items_retained   — Version-A items still justified by full data
  items_removed             — partial-data artifacts
  new_items_added           — what the remaining games revealed
  concept_rank_changes      — which weaknesses moved up/down
  position_fen_overlap      — whether examples were redundant across versions
  stage_distribution        — Repair/Reinforce/Transfer balance change
  priority_changes          — whether the weakness model materially changed
  hanging_piece_still_1     — did the #1 weakness survive the full dataset?
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_BASELINE = ROOT / "data" / "chess" / "baseline" / "curriculum_A_322.jsonl"
_BASELINE_MISTAKES = ROOT / "data" / "chess" / "baseline" / "mistakes_at_625.jsonl"
_TRAINING = ROOT / "data" / "chess" / "training.jsonl"
_MANIFEST = ROOT / "scripts" / "curriculum_baseline_manifest.json"


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _verify_baseline_integrity() -> tuple[bool, str]:
    """Verify the on-disk baseline files match the tracked manifest hashes.
    A mismatch means the 'immutable' Version A reference was modified — the
    A/B comparison would then be invalid (tamper detection without committing
    the dataset itself)."""
    if not _MANIFEST.exists():
        return False, f"manifest missing: {_MANIFEST}"
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    files = manifest.get("snapshot_files", {})
    for name, meta in files.items():
        path = ROOT / meta["path"]
        if not path.exists():
            return False, f"baseline file missing: {path}"
        actual = _sha256(path)
        if actual != meta["sha256"]:
            return False, (
                f"SHA-256 mismatch for {name}:\n"
                f"  manifest: {meta['sha256']}\n"
                f"  on disk : {actual}\n"
                f"The Version A baseline was modified — the comparison is invalid."
            )
    return True, "baseline verified: on-disk files match the manifest hashes"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _build_full_curriculum() -> list[dict]:
    """Rebuild the curriculum from the COMPLETE mistake store + GM moments.
    Mirrors the build functions in chess_training (idempotent on a fresh file).
    Returns the list of items that WOULD be generated from the full dataset."""
    import tempfile

    import swarm_os.services.chess_training as ct

    # Point the training store at a temp file so we can measure the full build
    # without touching the live curriculum.
    tmp = tempfile.NamedTemporaryFile(
        prefix="curriculum_b_", suffix=".jsonl", delete=False
    )
    tmp.close()
    old_store = ct._STORE_FILE
    ct._STORE_FILE = Path(tmp.name)
    ct.reset_all()
    try:
        ct.build_items_from_mistakes()
        ct.build_items_from_gm()
        return ct._load()
    finally:
        ct._STORE_FILE = old_store
        Path(tmp.name).unlink(missing_ok=True)


def _item_key(it: dict) -> tuple:
    """A stable identity for an item: (concept, stage, pre_fen)."""
    return (it.get("concept", ""), it.get("stage", ""), it.get("pre_fen", ""))


def compare() -> dict:
    # Integrity gate: refuse to report unless the frozen Version A baseline
    # still matches the tracked manifest hashes (tamper detection).
    ok, msg = _verify_baseline_integrity()
    if not ok:
        return {"ok": False, "integrity": msg}

    version_a = _load_jsonl(_BASELINE)
    version_b = _build_full_curriculum()

    a_keys = {_item_key(it) for it in version_a}
    b_keys = {_item_key(it) for it in version_b}

    retained = a_keys & b_keys
    removed = a_keys - b_keys
    added = b_keys - a_keys

    a_concepts = Counter(it.get("concept", "?") for it in version_a)
    b_concepts = Counter(it.get("concept", "?") for it in version_b)
    a_rank = {c: i for i, (c, _) in enumerate(a_concepts.most_common())}
    b_rank = {c: i for i, (c, _) in enumerate(b_concepts.most_common())}

    a_stages = Counter(it.get("stage", "?") for it in version_a)
    b_stages = Counter(it.get("stage", "?") for it in version_b)

    # FEN overlap: same positions used as examples across both versions.
    a_fens = {it.get("pre_fen", "") for it in version_a}
    b_fens = {it.get("pre_fen", "") for it in version_b}
    fen_overlap = a_fens & b_fens

    rank_changes = {}
    for c in set(a_rank) | set(b_rank):
        ra = a_rank.get(c)
        rb = b_rank.get(c)
        if ra is not None and rb is not None and ra != rb:
            rank_changes[c] = {"from": ra + 1, "to": rb + 1}
        elif c not in b_rank:
            rank_changes[c] = {"from": ra + 1, "to": None}  # dropped out

    return {
        "version_a_items": len(version_a),
        "version_b_items": len(version_b),
        "original_items_retained": len(retained),
        "items_removed": len(removed),
        "new_items_added": len(added),
        "retention_pct": round(100.0 * len(retained) / max(1, len(version_a)), 1),
        "concept_rank_changes": rank_changes,
        "dramatic_rank_changes": {
            c: v
            for c, v in rank_changes.items()
            if (v.get("to") is None) or (abs(v.get("to", 0) - v.get("from", 0)) >= 3)
        },
        "a_top_concepts": a_concepts.most_common(6),
        "b_top_concepts": b_concepts.most_common(6),
        "hanging_piece_still_1": (
            b_concepts.most_common(1)[0][0] if b_concepts else None
        )
        == "hanging piece",
        "position_fen_overlap": len(fen_overlap),
        "fen_overlap_examples": list(fen_overlap)[:10],
        "stage_distribution_a": dict(a_stages),
        "stage_distribution_b": dict(b_stages),
    }


def capture_full_evidence() -> dict:
    """Capture the complete pre-rebuild evidence state from the FINAL (842/842)
    dataset — everything needed to audit the coach's conclusions against the
    underlying data. This is the immutable record captured BEFORE Version B is
    rebuilt (Version A stays the control group; this is the full-data context).
    Also rebuilds Version B and produces the A/B comparison in one atomic
    report so the artifact is internally consistent."""
    from swarm_os.services import chess_mistakes as cm
    from swarm_os.services import chess_training as ct
    from swarm_os.services import chess_analysis_job as cj

    # 1. Final job state (must be complete).
    jobs = cj.list_jobs().get("jobs", [])
    running = [j for j in jobs if j.get("done_games", 0) < j.get("total_games", 1)]
    if running:
        j = running[0]
        return {
            "ok": False,
            "error": f"analysis not complete: {j.get('done_games')}/{j.get('total_games')}",
        }
    done_job = max(jobs, key=lambda j: j.get("done_games", 0))
    final_mistakes_queued = done_job.get("mistakes_queued", 0)

    # 2. Final mistake count + concept distribution + concept scores from the
    #    COMPLETE mistake store.
    mistakes = cm._load()
    mistake_count = len(mistakes)
    concept_dist: Counter = Counter()
    for e in mistakes:
        concept_dist[
            cm._classify_concept(
                e.get("pre_fen", ""),
                e.get("played_uci", ""),
                e.get("best_uci"),
                e.get("classification", ""),
            )
        ] += 1
    concept_scores = cm.coach_report().get("concept_scores", {})

    # 3. Top weakness + hanging-piece rank (1-indexed).
    top_weakness = concept_dist.most_common(1)[0] if concept_dist else None
    hanging_rank = next(
        (
            i + 1
            for i, (c, _) in enumerate(concept_dist.most_common())
            if c == "hanging piece"
        ),
        None,
    )

    # 4. Mastery state of the frozen curriculum.
    mastery = ct.concept_progress()

    # 5. A/B comparison (Version A frozen vs Version B full-data rebuild).
    comparison = compare()

    import datetime as _dt

    evidence = {
        "ok": True,
        "captured_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "analysis_job": {
            "done_games": done_job.get("done_games"),
            "total_games": done_job.get("total_games"),
            "mistakes_queued_final": final_mistakes_queued,
        },
        "final_mistake_count": mistake_count,
        "final_concept_distribution": dict(concept_dist.most_common()),
        "final_concept_scores": concept_scores,
        "top_weakness": {
            "concept": top_weakness[0] if top_weakness else None,
            "count": top_weakness[1] if top_weakness else 0,
        },
        "hanging_piece_rank": hanging_rank,
        "mastery_state": mastery,
        "ab_comparison": comparison,
    }
    return evidence


def write_artifact(evidence: dict) -> Path:
    """Write the full evidence report to a durable artifact file for audit."""
    out = (
        ROOT
        / "data"
        / "chess"
        / "baseline"
        / f"full_evidence_{evidence.get('analysis_job', {}).get('done_games', 0)}games.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    # Integrity check first: the frozen Version A baseline must match the
    # tracked manifest hashes, or the comparison is invalid.
    ok, msg = _verify_baseline_integrity()
    print("INTEGRITY:", msg)
    if not ok:
        sys.exit(1)
    # Capture the FULL pre-rebuild evidence + A/B comparison (this also
    # refuses if the analysis hasn't reached 842/842).
    evidence = capture_full_evidence()
    if not evidence.get("ok"):
        print("NOT READY:", evidence.get("error", "unknown"))
        sys.exit(1)
    artifact = write_artifact(evidence)
    print(f"FULL EVIDENCE + A/B COMPARISON written to: {artifact}")
    print()
    # Human-readable summary.
    ab = evidence["ab_comparison"]
    print(
        f"Final mistake count: {evidence['final_mistake_count']}  (job queued: {evidence['analysis_job']['mistakes_queued_final']})"
    )
    print(
        f"Top weakness: {evidence['top_weakness']['concept']} ({evidence['top_weakness']['count']})"
    )
    print(f"Hanging-piece rank: #{evidence['hanging_piece_rank']}")
    print(
        f"A={ab['version_a_items']} items -> B={ab['version_b_items']} items | "
        f"retained={ab['original_items_retained']} removed={ab['items_removed']} added={ab['new_items_added']} "
        f"({ab['retention_pct']}% retained)"
    )
    print(f"Hanging piece still #1 in full data: {ab['hanging_piece_still_1']}")
    if ab["dramatic_rank_changes"]:
        print("Dramatic rank changes:", json.dumps(ab["dramatic_rank_changes"]))
