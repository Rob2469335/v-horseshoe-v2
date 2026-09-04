"""V6 ChatML assembler — retrieval-context upgrade over V5.

Research grounding (arXiv:2604.05481, per AGENTS.md V6 audit): repair gain comes
from FILE-LEVEL localization context — successful repairs cluster at ~6-10
relevant files; MORE files helps, MORE line-level context degrades (noise).
The V5 assembler (get_prefix_code) handed the model only the modified file(s),
a massive localization hint but no surrounding context. V6 retrieves a small set
of relevant files (modified + AST import dependencies + reverse dependents) and
token-budgets them: the primary file generously, context files compactly.

Reads:  v5_audited_traces.jsonl   (accepted {source_commit, raw_extracted})
Writes: real_68_dataset_v6.jsonl  ({"text": "..."} plain User:/Assistant:)

This is an ASSEMBLER change only — it reuses the V5 audited traces (the
DIAGNOSIS/PLAN targets are unchanged); only the CODE CONTEXT input is richer.
"""
import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path

try:
    from transformers import AutoTokenizer
    _TOKENIZER = AutoTokenizer.from_pretrained(
        r"C:\Users\rober\models\Qwen3.5-4B-Base-HF", local_files_only=True)
except Exception:
    _TOKENIZER = None

REPO = r"C:/Users/rober/Projects/v-horseshoe-v2"
AUDIT = Path(r"C:/Users/rober/Projects/qwen_train_data/v6_audited_traces.jsonl")
OUT = Path(r"C:/Users/rober/Projects/qwen_train_data/real_68_dataset_v6.jsonl")

MAX_FILES = int(os.environ.get("V6_MAX_CONTEXT_FILES") or 10)
MAX_TOKENS = int(os.environ.get("V6_CODE_TOKEN_BUDGET") or 1500)
CONTEXT_FILE_LINES = int(os.environ.get("V6_CONTEXT_FILE_LINES") or 25)
PRIMARY_MAX_LINES = int(os.environ.get("V6_PRIMARY_MAX_LINES") or 150)
# Hard ceiling per assembled record (tokens) — keep under the ~2528
# max_seq_length OOM wall with headroom (AGENTS.md: operate with headroom).
TOTAL_TOKEN_CAP = int(os.environ.get("V6_TOTAL_TOKEN_CAP") or 2300)


def _tokens(text):
    if _TOKENIZER is not None:
        return len(_TOKENIZER.encode(text, add_special_tokens=False))
    return len(text) // 3


def _git(args):
    try:
        return subprocess.check_output(
            args, shell=True, cwd=REPO, stderr=subprocess.STDOUT
        ).decode("utf-8", errors="ignore")
    except subprocess.CalledProcessError:
        return ""


def _git_show(rev, path):
    return _git(f"git show {rev}:{path}")


def _exists_at(rev, path):
    try:
        subprocess.check_output(
            f"git cat-file -e {rev}:{path}", shell=True, cwd=REPO,
            stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False


def _modified_code_files(commit):
    out = _git(f"git diff-tree --no-commit-id --name-only -r {commit}")
    files = []
    for f in out.strip().split("\n"):
        f = f.strip()
        if not f or not f.endswith(".py"):
            continue
        if f.startswith("test") or "test_" in f or "/tests/" in f:
            continue
        files.append(f)
    return files


def _imports_of(content):
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.append(node.module)
    return mods


def _resolve_module(module, commit):
    """Resolve a dotted module name to a repo file path at commit~1 (or '')."""
    base = module.replace(".", "/")
    for cand in (base + ".py", base + "/__init__.py"):
        if _exists_at(f"{commit}~1", cand):
            return cand
    return ""


def _dependents_of(module, commit, cap=12):
    """Best-effort reverse-import lookup via git grep at commit~1."""
    pat = f"(from {module} import|import {module})"
    out = _git(f"git grep -l -E \"{pat}\" {commit}~1 -- *.py")
    deps = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if line and line.endswith(".py"):
            deps.append(line)
        if len(deps) >= cap:
            break
    return deps


def _truncate_lines(content, max_lines):
    lines = content.split("\n")
    if len(lines) <= max_lines:
        return content
    return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"


def get_retrieval_context(commit, budget=None):
    budget = budget if budget is not None else MAX_TOKENS
    modified = _modified_code_files(commit)
    if not modified:
        return ""
    primary = modified[0]
    rev = f"{commit}~1"

    ranked = []
    seen = set()
    for f in modified:
        if f not in seen:
            ranked.append(f)
            seen.add(f)

    primary_content = _git_show(rev, primary)
    for mod in _imports_of(primary_content):
        if len(ranked) >= MAX_FILES * 2:
            break
        path = _resolve_module(mod, commit)
        if path and path not in seen:
            ranked.append(path)
            seen.add(path)

    blocks = []
    tok_used = 0
    for idx, path in enumerate(ranked):
        if len(blocks) >= MAX_FILES or tok_used >= budget:
            break
        content = _git_show(rev, path)
        if not content.strip():
            continue
        if idx == 0:
            content = _truncate_lines(content, PRIMARY_MAX_LINES)
        else:
            content = _truncate_lines(content, CONTEXT_FILE_LINES)
        block = f"--- FILE: {path} ---\n{content}"
        bt = _tokens(block)
        if tok_used + bt > budget:
            remaining = budget - tok_used
            if remaining < 60:
                break
            keep = max(1, CONTEXT_FILE_LINES // 2)
            content = _truncate_lines(content, keep)
            block = f"--- FILE: {path} ---\n{content}"
            bt = _tokens(block)
            if tok_used + bt > budget:
                break
        blocks.append(block)
        tok_used += bt
    return "\n\n".join(blocks)


def extract_target(raw_extracted):
    idx = raw_extracted.find("DIAGNOSIS:")
    if idx < 0:
        raise ValueError(f"DIAGNOSIS: not found in raw_extracted (len={len(raw_extracted)})")
    return raw_extracted[idx:].strip()


def build_user(raw_extracted, code_context):
    task = evidence = ""
    curr = None
    curr_lines = []
    for line in raw_extracted.split("\n"):
        if line.startswith("TASK:"):
            curr = "TASK"; curr_lines = [line.replace("TASK:", "").strip()]
        elif line.startswith("EVIDENCE:"):
            if curr == "TASK": task = "\n".join(curr_lines).strip()
            curr = "EVIDENCE"; curr_lines = [line.replace("EVIDENCE:", "").strip()]
        elif line.startswith("FILES:") or line.startswith("DIAGNOSIS:"):
            if curr == "EVIDENCE": evidence = "\n".join(curr_lines).strip()
            curr = None
        elif curr:
            curr_lines.append(line)
    if curr == "EVIDENCE": evidence = "\n".join(curr_lines).strip()
    elif curr == "TASK": task = "\n".join(curr_lines).strip()
    prompt = ""
    if task: prompt += f"TASK:\n{task}\n\n"
    if evidence: prompt += f"EVIDENCE:\n{evidence}\n\n"
    if not (task or evidence):
        prompt = raw_extracted.split("FILES:")[0].strip() + "\n\n"
    return f"{prompt}CODE CONTEXT (Pre-fix state):\n{code_context}"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Assemble V6 retrieval-context dataset")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Only process first N (debug)")
    ap.add_argument("--out", default=None)
    argv = ap.parse_args()

    if not AUDIT.exists():
        raise SystemExit(f"FATAL: {AUDIT} not found. Run audit_traces_v5.py first.")
    rows = [json.loads(l) for l in AUDIT.read_text(encoding="utf-8").splitlines() if l.strip()]
    if argv.limit:
        rows = rows[: argv.limit]

    assembled = []
    file_counts = []
    for r in rows:
        commit = r["source_commit"].strip()
        raw = r.get("raw_extracted", "")
        try:
            target = extract_target(raw)
            base_user = build_user(raw, "")
            base_tokens = _tokens(base_user) + _tokens(target) + 30
            budget = max(200, min(MAX_TOKENS, TOTAL_TOKEN_CAP - base_tokens))
            code = get_retrieval_context(commit, budget=budget)
        except ValueError as e:
            raise SystemExit(f"FATAL: {e}\n  record: {commit}")
        if not target:
            raise SystemExit(f"FATAL: empty target for {commit}")
        nfiles = code.count("--- FILE:")
        file_counts.append(nfiles)
        user_msg = build_user(raw, code)
        text = f"User: {user_msg.strip()}\n\nAssistant:\n{target.strip()}"
        assembled.append({"text": text, "_source_commit": commit,
                          "_nfiles": nfiles, "_user_nchars": len(user_msg)})

    import statistics
    print(f"accepted traces: {len(rows)}")
    print(f"assembled: {len(assembled)}")
    if file_counts:
        print(f"context files/record: min={min(file_counts)} "
              f"median={statistics.median(file_counts)} max={max(file_counts)}")
        print(f"records with >=3 context files: {sum(1 for c in file_counts if c >= 3)}/{len(file_counts)}")
    toks = sorted(_tokens(a["text"]) for a in assembled)
    if toks:
        print(f"token estimate/record: min={toks[0]} median={toks[len(toks)//2]} max={toks[-1]}")
        print(f"records over 2400 tokens: {sum(1 for t in toks if t > 2400)}")

    if assembled and argv.dry_run:
        ex = assembled[0]
        print("\n" + "#" * 78)
        print(f"# DRY-RUN example (commit {ex['_source_commit'][:12]}, {ex['_nfiles']} context files)")
        print("#" * 78)
        print(ex["text"][:1800])
        print("\n...[truncated]...")

    out_path = Path(argv.out) if argv.out else OUT
    if argv.dry_run:
        print(f"\nDRY-RUN OK — would write {out_path}: {len(assembled)} records")
        return
    fd, tmppath = tempfile.mkstemp(dir=str(out_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for a in assembled:
                f.write(json.dumps({"text": a["text"]}, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmppath, out_path)
    except BaseException:
        if os.path.exists(tmppath):
            os.remove(tmppath)
        raise
    print(f"\nWROTE {out_path}: {len(assembled)} records")


if __name__ == "__main__":
    main()
