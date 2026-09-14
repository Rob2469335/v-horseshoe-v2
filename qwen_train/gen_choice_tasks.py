"""Multi-tool-CHOICE task generators — the real diversity fix.

The mined pool is ~90% single-tool (sandbox_repl | filesystem): no tool *choice* to
learn. These families are genuinely ambiguous (2-3 viable tools) AND machine-verified
with an exact ground truth, so the tool policy has something to distinguish and the
verifier is trustworthy.

Read-only over the repo. Writes its own file (never touches generated.jsonl/raw pool).

Families (target_tools — the viable routes):
  line_count      filesystem · sandbox_repl · system
  token_count     filesystem · sandbox_repl · semantic_search
  referencing     filesystem · lsp · semantic_search
  multi_def       filesystem · lsp · semantic_search
  importers       filesystem · lsp · semantic_search
  longest_of      filesystem · sandbox_repl
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc  # noqa: E402

OUT = _HERE / "curriculum" / "choice.jsonl"


def _scan(root: Path, limit: int = 200) -> dict:
    files = rc._repo_files(root, limit=limit)
    texts: dict[str, str] = {}
    defs: dict[str, set] = collections.defaultdict(set)
    imports: dict[str, set] = collections.defaultdict(set)
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        texts[rel] = text
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[node.name].add(rel)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.name.split(".")[0]].add(rel)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports[node.module.split(".")[0]].add(rel)
    return {
        "texts": texts,
        "defs": defs,
        "imports": imports,
    }


def _token_count(texts: dict[str, str], name: str) -> int:
    pat = re.compile(rf"\b{re.escape(name)}\b")
    return sum(len(pat.findall(t)) for t in texts.values())


def _item(i: int, tools: list[str], difficulty: int, prompt: str, value) -> dict:
    vals = value if isinstance(value, list) else [value]
    return {
        "id": f"x{i:05d}",
        "split": "train",
        "difficulty": difficulty,
        "target_tools": tools,
        "prompt": prompt,
        "verify": {"type": "contains", "mode": "any", "value": [str(v) for v in vals]},
    }


def generate(root: Path, n_per_family: int = 60) -> list[dict]:
    scan = _scan(root)
    texts, defs, imports = (
        scan["texts"],
        scan["defs"],
        scan["imports"],
    )
    out: list[dict] = []
    ctr = 0

    # --- line_count: exact integer ---
    rels = sorted(texts)
    for rel in rels[:n_per_family]:
        lc = len(texts[rel].splitlines())
        out.append(
            _item(ctr, ["filesystem", "sandbox_repl", "system"], 1,
                  f"How many lines does the file `{rel}` contain? Answer with the number.", lc)
        )
        ctr += 1

    # --- token_count: exact integer (whole-word occurrences across all .py) ---
    uniq_defs = [(nm, next(iter(fs))) for nm, fs in defs.items() if len(fs) == 1 and len(nm) >= 6]
    counted = 0
    for nm, _d in sorted(uniq_defs):
        if counted >= n_per_family:
            break
        n = _token_count(texts, nm)
        if n < 2:
            continue
        out.append(
            _item(ctr, ["filesystem", "sandbox_repl", "semantic_search"], 2,
                  f"How many times does the identifier `{nm}` appear as a whole word across all "
                  f"project .py files? Answer with the number.", n)
        )
        ctr += 1
        counted += 1

    # --- referencing: name one OTHER file containing the identifier ---
    counted = 0
    for nm, df in sorted(uniq_defs):
        if counted >= n_per_family:
            break
        pat = re.compile(rf"\b{re.escape(nm)}\b")
        others = [r for r, t in texts.items() if r != df and pat.search(t)]
        if not others:
            continue
        out.append(
            _item(ctr, ["filesystem", "lsp", "semantic_search"], 3,
                  f"Name one file (other than `{df}`) that references the identifier `{nm}`. "
                  f"Answer with the file path.", others)
        )
        ctr += 1
        counted += 1

    # --- multi_def: is the name defined in more than one file? yes/no ---
    counted = 0
    # include some genuinely multi-def names (answer yes) and some single (no)
    multi = sorted(nm for nm, fs in defs.items() if len(fs) > 1 and len(nm) >= 6)
    single = sorted(nm for nm, fs in defs.items() if len(fs) == 1 and len(nm) >= 6)
    for nm, ans in [(x, "yes") for x in multi[: n_per_family]] + [(x, "no") for x in single[: n_per_family]]:
        out.append(
            _item(ctr, ["filesystem", "lsp", "semantic_search"], 2,
                  f"Is the identifier `{nm}` defined as a def/class in MORE THAN ONE file? Answer yes or no.",
                  ans)
        )
        ctr += 1

    # --- importers: name one file that imports the module ---
    counted = 0
    for mod, imps in sorted(imports.items()):
        if counted >= n_per_family:
            break
        if not mod.isidentifier() or len(mod) < 3:
            continue
        out.append(
            _item(ctr, ["filesystem", "lsp", "semantic_search"], 2,
                  f"Name one file that imports the module `{mod}`. Answer with the file path.",
                  sorted(imps))
        )
        ctr += 1
        counted += 1

    # --- longest_of: which of 3 files has the most lines ---
    counted = 0
    for i in range(0, min(len(rels) - 2, n_per_family * 3), 3):
        trio = rels[i : i + 3]
        if len(trio) < 3:
            break
        longest = max(trio, key=lambda r: len(texts[r].splitlines()))
        out.append(
            _item(ctr, ["filesystem", "sandbox_repl"], 2,
                  "Of these files, which has the most lines: "
                  + ", ".join(f"`{r}`" for r in trio)
                  + "? Answer with the file path.", longest)
        )
        ctr += 1
        counted += 1

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate multi-tool-choice tasks")
    ap.add_argument("--n-per-family", type=int, default=60)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    items = generate(rc._HERE.parent, args.n_per_family)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for it in items:
            json.dump(it, fh, ensure_ascii=False)
            fh.write("\n")

    fams = collections.Counter("|".join(sorted(it["target_tools"])) for it in items)
    print(f"generated {len(items)} multi-choice items -> {out.relative_to(_HERE.parent)}")
    for k, v in fams.most_common():
        print(f"  {v:5d}  {k}")
    print("\nwire into run_curriculum.load_items (or --pool) post-run; then balance via diverse_select.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
