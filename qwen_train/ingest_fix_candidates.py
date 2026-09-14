"""Ingest a candidate bug-kind pool and verify SOUNDNESS before it enters the curriculum.

Takes the markdown pool authored by an external model (Opus/Sonnet; format defined in
the Opus prompt) OR a JSONL pool, and for each candidate independently proves:

  BROKEN module  -> check.py must FAIL   (the defect is real and the check catches it)
  FIXED  module  -> check.py must PASS   (the check actually pins the defect, not trivially)

Only candidates that pass BOTH are emitted as usable kinds. This is the gate that stops
an unsound verifier (a "bug" whose check always passes, or a fix that doesn't fix) from
silently entering the benchmark — the failure mode a green agent run cannot detect.

Usage:
  python qwen_train/ingest_fix_candidates.py --in pool.md --out sound.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def parse_markdown(text: str) -> list[dict]:
    """Parse the '### kind / family / difficulty / why_hard / 3 python fences' format."""
    out: list[dict] = []
    for chunk in re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]:
        lines = chunk.splitlines()
        kind = lines[0].strip()
        meta = {}
        for ln in lines[1:12]:
            m = re.match(r"\s*(family|difficulty|why_hard)\s*:\s*(.+)$", ln)
            if m:
                meta[m.group(1)] = m.group(2).strip()
        fences = _FENCE.findall(chunk)
        if len(fences) < 3:
            out.append({"kind": kind, "error": f"expected 3 code fences, got {len(fences)}"})
            continue
        out.append(
            {
                "kind": kind,
                "family": meta.get("family", "?"),
                "difficulty": int(meta.get("difficulty", "3") or 3),
                "why_hard": meta.get("why_hard", ""),
                "broken": fences[0],
                "fixed": fences[1],
                "check": fences[-1],
            }
        )
    return out


def _run_check(dirpath: Path, module_src: str, check_src: str, timeout: int = 30) -> int:
    (dirpath / "module.py").write_text(module_src, encoding="utf-8")
    (dirpath / "check.py").write_text(check_src, encoding="utf-8")
    import os

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        p = subprocess.run(
            [sys.executable, "check.py"],
            cwd=str(dirpath),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return p.returncode
    except subprocess.TimeoutExpired:
        return -999


def check_soundness(cand: dict, timeout: int = 30) -> dict:
    """BROKEN must fail (nonzero) AND FIXED must pass (zero).

    Each run uses a SEPARATE temp dir: broken and fixed sources can be the same byte
    length (e.g. `x - 1` vs `x + 1`), and a shared dir would let Python reuse the stale
    `__pycache__/module*.pyc` for the second run — a false 'unsound'.
    """
    if "error" in cand:
        return {"sound": False, "reasons": [cand["error"]]}
    reasons: list[str] = []
    with tempfile.TemporaryDirectory() as td_broken, tempfile.TemporaryDirectory() as td_fixed:
        rc_broken = _run_check(Path(td_broken), cand["broken"], cand["check"], timeout)
        rc_fixed = _run_check(Path(td_fixed), cand["fixed"], cand["check"], timeout)
    if rc_broken == 0:
        reasons.append("BROKEN module PASSED its check (defect not caught / check too loose)")
    if rc_fixed != 0:
        reasons.append(f"FIXED module FAILED its check (rc={rc_fixed}) — reference fix is wrong")
    return {
        "sound": not reasons,
        "rc_broken": rc_broken,
        "rc_fixed": rc_fixed,
        "reasons": reasons,
    }


def ingest(path: Path, timeout: int = 30) -> tuple[list[dict], list[dict]]:
    text = Path(path).read_text(encoding="utf-8")
    cands = parse_markdown(text) if "###" in text else [
        json.loads(ln) for ln in text.splitlines() if ln.strip()
    ]
    sound, unsound = [], []
    for c in cands:
        verdict = check_soundness(c, timeout)
        rec = {**c, **verdict}
        (sound if verdict["sound"] else unsound).append(rec)
    return sound, unsound


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest + soundness-gate a fix-candidate pool")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", default="qwen_train/curriculum/fix_candidates_sound.jsonl")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    sound, unsound = ingest(Path(args.src), args.timeout)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for c in sound:
            json.dump(c, fh, ensure_ascii=False)
            fh.write("\n")

    print(f"parsed: {len(sound) + len(unsound)}  SOUND: {len(sound)}  UNSOUND: {len(unsound)}")
    for c in unsound:
        print(f"  UNSOUND [{c['kind']}]: {'; '.join(c['reasons'])}")
    import collections

    print("sound by family:", dict(collections.Counter(c.get("family", "?") for c in sound)))
    print(f"wrote sound pool -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
