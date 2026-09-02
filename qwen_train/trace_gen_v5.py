"""V5 trace generator — 67 verified single-file .py commits -> real_68_dataset_v1.jsonl

Method (per AGENTS.md V4 trace-extraction docs, upgraded with V5 lessons):
- base model (qwen3.5-4b-base-q4km.gguf on :8086), temp 0
- pure zero-shot prompt (no 1-shot — documented recency-bias failure)
- --reasoning-budget 1200 (proven V5 serving fix: localization completes)
- NATIVELY prompt the path-led DIAGNOSIS format: "File: <path> — <diagnosis>"
  (NOT a post-hoc splice — the diagfix null result showed splicing doesn't transfer)
- output schema: {source_commit, raw_extracted} matching real_25_dataset_v1.jsonl

For each commit:
1. git show <commit>~1:<file>  -> pre-fix source
2. git show <commit> -- <file> -> the fix diff (what the bug was)
3. prompt base model: pre-fix code + diff + path-led format rule
4. write row
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx

COMMITS_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:/Users/rober/AppData/Local/Temp/opencode/v5_commits.json")
OUT = Path(r"C:/Users/rober/Projects/qwen_train_data/real_68_dataset_v1.jsonl")
REPO = r"C:/Users/rober/Projects/v-horseshoe-v2"
PORT = 8086
MODEL = "qwen3.5-4b"  # base model alias on :8086

SYSTEM_PROMPT = (
    "You are an expert developer diagnosing a real bug in a real codebase. "
    "Analyze the code and the fix diff below, then produce a diagnostic report.\n"
    "Your DIAGNOSIS must begin with EXACTLY the file path as its first token:\n"
    "DIAGNOSIS:\n"
    "File: <the_relevant_file_path> \u2014 <your concise diagnosis of the root cause>\n\n"
    "Then include sections: EVIDENCE, FILES, PLAN, RISKS, VERIFICATION, FAILED_APPROACH.\n"
    "EVIDENCE cites the concrete evidence in the code/diff. FILES lists each file with what changed. "
    "PLAN is the precise fix. VERIFICATION describes how the fix is tested. "
    "FAILED_APPROACH is what did NOT work (or UNKNOWN)."
)


def git(*args):
    return subprocess.check_output(list(args), cwd=REPO, shell=False).decode("utf-8", "ignore")


def build_prompt(commit, file_path):
    pre = git("git", "show", f"{commit}~1:{file_path}")
    diff = git("git", "show", commit, "--", file_path)
    return (
        f"BUG REPORT\n"
        f"Target file (pre-fix state):\n```\n{pre[:6000]}\n```\n\n"
        f"The fix applied to this file (the diff that resolved the bug):\n"
        f"```diff\n{diff[:6000]}\n```\n\n"
        f"Diagnose the bug that the diff fixes. Start your DIAGNOSIS with 'File: {file_path} \u2014'."
    )


def trace(client, commit, file_path):
    prompt = build_prompt(commit, file_path)
    # REPO_CONTEXT injection (Task 1): opt-in via V5_REPO_CONTEXT=1 so the current
    # live trace run (started without it) stays consistent. Enabled for future runs.
    if os.environ.get("V5_REPO_CONTEXT") == "1":
        try:
            from repo_context import build_prompt_with_context
            prompt = build_prompt_with_context(prompt, enabled=True)
        except Exception:
            pass  # never break a trace run if the context import fails
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2048,
        "temperature": 0.0,
    }
    r = client.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=payload, timeout=400.0)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    reasoning = (msg.get("reasoning_content") or "").strip()
    content = (msg.get("content") or "").strip()
    # assemble raw_extracted the way v1 does: source_commit header + the answer
    blob = f"SOURCE_COMMIT: {commit}\n\n" + content
    return blob, reasoning, content


def main():
    commits = json.loads(COMMITS_FILE.read_text(encoding="utf-8")) if COMMITS_FILE.exists() else []
    if not commits:
        print("no commits loaded")
        sys.exit(1)
    print(f"commits to trace: {len(commits)}")
    client = httpx.Client(timeout=400.0)
    rows = []
    done_commits = set()
    # resume support: skip commits already in the output file
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("source_commit"):
                    done_commits.add(r["source_commit"])
                    rows.append(r)
    print(f"resuming: {len(done_commits)} already traced, {len(commits) - len(done_commits)} remaining", flush=True)
    for i, c in enumerate(commits):
        if c["sha"] in done_commits:
            continue
        print(f"[{i+1}/{len(commits)}] {c['sha'][:8]} {c['path']}", flush=True)
        try:
            blob, reasoning, content = trace(client, c["sha"], c["path"])
            if not content:
                print("  !! empty content — server may be down or reasoning only", flush=True)
                rows.append({"source_commit": c["sha"], "raw_extracted": blob, "trace_error": "empty_content"})
            else:
                rows.append({"source_commit": c["sha"], "raw_extracted": blob})
                print(f"  content={len(content)}ch", flush=True)
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
            rows.append({"source_commit": c["sha"], "raw_extracted": "", "trace_error": str(e)})
        # incremental write after each commit (crash-safe)
        OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        time.sleep(0.5)
    client.close()
    print(f"\nwrote {OUT}: {len(rows)} rows ({sum(1 for r in rows if r.get('raw_extracted'))} with content)")


if __name__ == "__main__":
    main()