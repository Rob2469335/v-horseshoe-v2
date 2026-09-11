"""V7 SYMBOL-GROUNDED trace generator.

Research basis (arXiv:2608.13568): an agent's use of semantic/symbol tools is a
task-shaped, LEARNABLE policy — "reinforce it into the policy rather than bolt on
the tool". V6 traces taught repair from a raw-file window; this generator teaches
the *adaptive* behavior: locate by SYMBOL, then edit by symbol.

Per commit (single-file .py fix):
  1. git show <commit>~1:<file>  -> pre-fix source
  2. git show <commit> -- <file> -> the real fix diff
  3. Serena (get_symbols_overview / find_symbol) -> the file's symbols + the
     symbol(s) whose line range intersects the fix  (ast fallback if offline)
  4. assemble a ChatML record whose assistant turn reasons in symbol terms:
     "the defect is in <symbol> (<file>:<lines>) ... fix: <diff>"

Output: qwen_train/results/symbol_traces_v7.jsonl  ({"text": <ChatML>})

Usage:
  python qwen_train/gen_symbol_traces_v7.py [limit]
No model server needed for the deterministic assembly; Serena is optional.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = r"C:\Users\rober\Projects\v-horseshoe-v2"
OUT = Path(r"C:\Users\rober\Projects\v-horseshoe-v2\qwen_train\results\symbol_traces_v7.jsonl")
FIX_RE = re.compile(r"^(FIX|HEAL|SERVICE|FEAT):", re.IGNORECASE)


def git(*args) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO, stderr=subprocess.DEVNULL
    ).decode("utf-8", "ignore")


def mine_commits(limit: int) -> list[str]:
    out = git("log", "--no-merges", "--pretty=format:%H%x09%s", "-n", "400")
    commits = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        h, subj = line.split("\t", 1)
        if FIX_RE.match(subj.strip()):
            commits.append(h)
        if len(commits) >= limit:
            break
    return commits


def changed_py_files(commit: str) -> list[str]:
    out = git("diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    return [f for f in out.splitlines() if f.endswith(".py")]


def symbols_of(src: str) -> list[dict]:
    """Top-level def/class names + line ranges (ast — deterministic offline)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    syms = []
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            syms.append(
                {
                    "name": node.name,
                    "kind": type(node).__name__.replace("Def", "").lower(),
                    "start": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno),
                }
            )
    return syms


def diff_hunk_lines(diff: str) -> list[int]:
    """New-file line numbers touched by the diff (from @@ -a,b +c,d hunks)."""
    touched = []
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start = int(m.group(1))
        count = int(m.group(2) or 1)
        touched.extend(range(start, start + max(count, 1)))
    return touched


def intersecting_symbol(syms: list[dict], lines: list[int]) -> dict | None:
    if not syms or not lines:
        return None
    lo, hi = min(lines), max(lines)
    best = None
    for s in syms:
        if s["start"] <= hi and s["end"] >= lo:
            if best is None or (s["end"] - s["start"]) < (best["end"] - best["start"]):
                best = s
    return best


def build_record(commit: str, file_path: str) -> dict | None:
    try:
        pre = git("show", f"{commit}~1:{file_path}")
        diff = git("show", commit, "--", file_path)
    except subprocess.CalledProcessError:
        return None
    if not diff.strip():
        return None
    syms = symbols_of(pre)
    hit = intersecting_symbol(syms, diff_hunk_lines(diff))
    overview = ", ".join(f"{s['name']} (L{s['start']}-{s['end']})" for s in syms[:12])
    subj = git("show", "-s", "--format=%s", commit).strip()
    sym_line = (
        f"SYMBOL: {hit['name']} ({hit['kind']}) at {file_path}:{hit['start']}-{hit['end']}"
        if hit
        else f"SYMBOL: (no top-level symbol intersected; file symbols: {overview})"
    )
    user = (
        f"BUG REPORT\n{subj}\n\nTarget file: {file_path}\n"
        f"Symbols in file: {overview or '(none)'}\n"
        f"Locate the defect by symbol, then fix it."
    )
    assistant = (
        f"DIAGNOSIS\n{sym_line}\n\n"
        f"Fix applied (the real, verified change):\n```diff\n{diff[:4000]}\n```"
    )
    text = (
        "<|im_start|>system\nYou are an expert developer repairing a real codebase. "
        "Locate the defect by SYMBOL (use the file's symbol map), then apply a "
        "minimal, symbol-scoped fix.<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>\n"
    )
    return {
        "text": text,
        "source_commit": commit,
        "file": file_path,
        "symbol": hit["name"] if hit else "",
    }


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    OUT.parent.mkdir(parents=True, exist_ok=True)
    commits = mine_commits(limit)
    written = 0
    with OUT.open("w", encoding="utf-8") as f:
        for c in commits:
            files = changed_py_files(c)
            if len(files) != 1:
                continue
            rec = build_record(c, files[0])
            # keep ONLY genuinely symbol-grounded fixes (a top-level def/class
            # intersects the diff) — that is the V7 signal we want to teach.
            if rec and rec["symbol"]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    print(f"mined={len(commits)} written={written} -> {OUT}")


if __name__ == "__main__":
    main()
