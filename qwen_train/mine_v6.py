"""V6 commit miner — build the ~300-example commit list for trace generation.

Strategy (one example per commit, preferring clean single-file fixes):
  - Every FIX:/HEAL:-prefixed commit with >=1 non-test .py file is a candidate.
  - Single-file commits -> that file (cleanest: one bug -> one file).
  - Multi-file commits  -> the primary file (largest changed-line span).
The grounding audit (audit_traces_v5.py) later rejects weak/hallucinated traces,
 so it is safer to over-mine here and let the audit filter.

Writes: qwen_train_data/v6_commits.json  (array of {"sha","path"})
"""
import json
import subprocess
from pathlib import Path

REPO = r"C:/Users/rober/Projects/v-horseshoe-v2"
OUT = Path(r"C:/Users/rober/Projects/qwen_train_data/v6_commits.json")
TARGET = 300


def git(args):
    try:
        return subprocess.check_output(
            args, shell=True, cwd=REPO, stderr=subprocess.STDOUT
        ).decode("utf-8", "ignore")
    except subprocess.CalledProcessError:
        return ""


def is_test_path(p):
    return p.startswith("test") or "test_" in p or "/tests/" in p or p.startswith("tests/")


def modified_py(commit):
    out = git(f"git diff-tree --no-commit-id --name-only -r {commit}")
    return [f.strip() for f in out.split("\n")
            if f.strip().endswith(".py") and not is_test_path(f.strip())]


def diff_span(commit, path):
    out = git(f"git diff --numstat {commit}~1 {commit} -- {path}")
    try:
        added, deleted = out.split("\t")[:2]
        return int(added) + int(deleted)
    except Exception:
        return 0


def main():
    lines = git('git log --pretty=format:"%H|%s"').split("\n")
    entries = []
    seen_sha = set()
    for line in lines:
        if "|" not in line:
            continue
        sha, subj = line.split("|", 1)
        sha = sha.strip().strip('"')
        subj = subj.strip()
        if not (subj.startswith("FIX:") or subj.startswith("HEAL:")):
            continue
        files = modified_py(sha)
        if not files:
            continue
        if len(files) == 1:
            path = files[0]
        else:
            path = max(files, key=lambda f: diff_span(sha, f))
        if sha in seen_sha:
            continue
        seen_sha.add(sha)
        entries.append({"sha": sha, "path": path, "subject": subj[:80]})
        if len(entries) >= TARGET:
            break

    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    single = sum(1 for e in entries if len(modified_py(e["sha"])) == 1)
    print(f"mined {len(entries)} commit examples -> {OUT}")
    print(f"  single-file: {single}  multi-file(primary): {len(entries) - single}")


if __name__ == "__main__":
    main()
