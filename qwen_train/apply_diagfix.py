"""Deterministic trace-format fix (2026-08-31): rewrite each V4 training
example's DIAGNOSIS line so it LEADS with the correct GT file path.

Rationale (from the eval): the adapter's path-string is lost across the
reasoning->final boundary worse than base; a learned `FILES:` footer doesn't
transfer (mechanism-check refuted FILES:-suppression). The targeted fix is to
make the DIAGNOSIS itself name the file, so the answer starts with the path.

Correct file per example = the non-test .py files changed by the source commit
(exact same rule `build_v4.py` uses to pick code context). Deterministic; no
trace regeneration needed.

Output: real_25_dataset_v4_diagfix.jsonl (same schema, text modified).
"""
import json
import subprocess
import re
from pathlib import Path

V1 = Path(r"C:/Users/rober/Projects/qwen_train_data/real_25_dataset_v1.jsonl")
V4 = Path(r"C:/Users/rober/Projects/qwen_train_data/real_25_dataset_v4.jsonl")
OUT = Path(r"C:/Users/rober/Projects/qwen_train_data/real_25_dataset_v4_diagfix.jsonl")
REPO = r"C:/Users/rober/Projects/v-horseshoe-v2"


def gt_files_for(commit: str) -> list[str]:
    try:
        files = subprocess.check_output(
            f"git diff-tree --no-commit-id --name-only -r {commit}",
            shell=True, cwd=REPO).decode("utf-8", errors="ignore").split()
    except subprocess.CalledProcessError:
        return []
    # mirror build_v4.py's filter: keep .py, drop test files
    return [f for f in files if f.endswith(".py") and "test" not in f.lower() and "tests/" not in f]


def splice_diagnosis(text: str, files: list[str]) -> str:
    if not files:
        return text
    path = files[0]
    marker = "DIAGNOSIS:"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == marker or ln.strip().startswith(marker):
            # find the first non-empty line after the marker (the diagnosis prose)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                lines[j] = f"File: {path} — {lines[j].strip()}"
            break
    return "\n".join(lines)


def main():
    v1_records = []
    for line in V1.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v1_records.append(json.loads(line))
    # v4 and v1 are POSITION-ALIGNED (verified 23/23 identity via DIAGNOSIS-body
    # substring match on both directions). Use the positional map — deterministic.
    out = []
    touched = 0
    missing_files = []
    v4_records = [json.loads(l) for l in V4.read_text(encoding="utf-8").splitlines() if l.strip()]
    for i, rec in enumerate(v4_records):
        if i >= len(v1_records):
            missing_files.append(rec)
            out.append(rec)
            continue
        commit = v1_records[i]["source_commit"]
        files = gt_files_for(commit)
        if not files:
            missing_files.append(rec)
            out.append(rec)
            continue
        new_text = splice_diagnosis(rec["text"], files)
        if new_text != rec["text"]:
            touched += 1
        rec["text"] = new_text
        out.append(rec)

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    print(f"wrote {OUT} : {len(out)} rows, {touched} DIAGNOSIS-spliced (positional map)")
    print(f"rows without commit/files: {len(missing_files)}")
    for m in missing_files[:5]:
        print("   unmatched:", m["text"][:80].replace("\n", " ")[:80])


def _diag_body(text: str) -> str:
    m = re.search(r"DIAGNOSIS:\s*\n([\s\S]*?)(?=\n\s*EVIDENCE:|\n\s*FILES:|\n\s*PLAN:|\n\s*VERIFICATION:|\n\s*VALIDATION:|\Z)", text)
    return m.group(1).strip() if m else ""


if __name__ == "__main__":
    main()