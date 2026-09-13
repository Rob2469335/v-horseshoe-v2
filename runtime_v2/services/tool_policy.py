"""Contextual tool policy for the harness (learns *which* tools to call).

Research basis (2026-09-13): the SOTA for "the system learns to call tools
better" is a *contextual*, experience-driven policy — not one global weight
vector. See the Tool Selection Optimization report (RL tool selection 20-70%
gains; cost-aware routing), BoR "How Many Tools Should an LLM Agent See?"
(arXiv:2605.24660, adaptive depth), and Online-Optimized RAG for tool use
(arXiv:2509.20415, adapt retrieval from live task-success).

This module learns per-(task-shape, tool) weights from VERIFIED outcomes
(`record_observation`), and exposes `rank` (reorder — always safe) and
`shortlist` (adaptive depth — flagged). It complements the existing coarse
`outcome_fitness` genome by attributing success to the tools actually used.

Fail-open by construction: any error returns the input tools unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

OBSERVATIONS = Path("data/evolution/tool_observations.jsonl")
_MAX_SCAN = 4000

# Tools never dropped by a shortlist (the safe floor for any task).
_CORE = ("final", "filesystem", "sandbox_repl", "web_search", "delegate")

_SHAPES = {
    "math": ("compute", "calculate", "sum of", "prime", "fibonacci", "gcd", "multiply"),
    "web": ("search the web", "internet", "latest", "news", "url", "fetch", "http"),
    "git": ("git", "branch", "commit", "diff", "blame"),
    "system": ("cpu", "process", "disk", "operating system", "platform", "installed"),
    "memory": ("memory", "remember", "recall", "what did we", "previous session"),
    "code": (
        "read",
        "file",
        "code",
        "function",
        "class",
        "bug",
        "fix",
        "refactor",
        "test",
        "import",
        "module",
        ".py",
        "repo",
        "documentation",
    ),
}


def shape_of(task: str) -> str:
    """Deterministic keyword shape for a task (small, stable set)."""
    text = str(task or "").lower()
    if any(k in text for k in ("*", " + ", " - ", "/")) and any(
        c.isdigit() for c in text
    ):
        # a bare arithmetic prompt
        if any(k in text for k in _SHAPES["math"]):
            return "math"
    for shape in ("math", "git", "system", "memory", "web", "code"):
        if any(k in text for k in _SHAPES[shape]):
            return shape
    return "other"


def record_observation(
    task: str, tools_used: list[str], verified, tool_ok: bool | None = None
) -> None:
    """Persist one verified experience: which tools were used + did it pass.

    ``tool_ok`` marks whether the TOOL itself executed successfully. When the
    tool succeeded but the answer still failed verification (``tool_ok is True
    and verified is False``) the failure is DOWNSTREAM (answer synthesis), NOT a
    tool failure — recording it would teach the policy a false correlation
    ("this tool doesn't work"). Such cases are skipped. A genuine tool failure
    (``tool_ok is False``) IS recorded. ``tool_ok=None`` (unknown) records as
    before, for backward compatibility.
    """
    if verified is None or not tools_used:
        return
    if tool_ok is True and not verified:
        return  # answer-synthesis failure: do not attribute it to the tool
    try:
        OBSERVATIONS.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "shape": shape_of(task),
            "tools": [str(t) for t in tools_used][:12],
            "verified": bool(verified),
        }
        with OBSERVATIONS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("tool_policy: observation write skipped: %s", exc)


def _load() -> list[dict]:
    if not OBSERVATIONS.exists():
        return []
    out: list[dict] = []
    try:
        for line in OBSERVATIONS.read_text(encoding="utf-8").splitlines()[-_MAX_SCAN:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def tool_weights(shape: str | None = None) -> dict[str, float]:
    """Laplace-smoothed success rate per tool (optionally within a shape)."""
    succ: dict[str, int] = {}
    total: dict[str, int] = {}
    for rec in _load():
        if shape is not None and rec.get("shape") != shape:
            continue
        ok = bool(rec.get("verified"))
        for tool in rec.get("tools") or []:
            total[tool] = total.get(tool, 0) + 1
            if ok:
                succ[tool] = succ.get(tool, 0) + 1
    return {t: (succ.get(t, 0) + 1) / (n + 2) for t, n in total.items()}


def _fitness_genes() -> dict:
    try:
        from swarm_os.services.evolution_daemon import get_active_genome

        _, genes = get_active_genome(False)
        return genes or {}
    except Exception:  # noqa: BLE001
        return {}


def _tool_docs() -> dict[str, str]:
    """tool name -> retrieval text (its own name + description, lowercased)."""
    try:
        from runtime_v2.prompts.system_prompts import _TOOL_DEFINITIONS

        return {
            t: (t.replace("_", " ") + " " + str(d)).lower()
            for t, d in _TOOL_DEFINITIONS.items()
        }
    except Exception:  # noqa: BLE001
        return {}


_WORD_RE = None
_STOP = {
    "the",
    "for",
    "and",
    "with",
    "use",
    "using",
    "via",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "is",
    "it",
    "its",
    "from",
    "into",
    "please",
    "your",
    "you",
    "me",
    "my",
    "then",
    "than",
    "that",
    "this",
    "and",
    "exact",
    "value",
    "report",
}


def _tokens(text: str) -> list[str]:
    global _WORD_RE
    if _WORD_RE is None:
        import re

        _WORD_RE = re.compile(r"[a-z0-9_]{2,}")
    return [w for w in _WORD_RE.findall(str(text or "").lower()) if w not in _STOP]


def lexical_scores(task: str, tools: list[str]) -> dict[str, float]:
    """IDF-weighted token overlap between the task and each tool's description.

    This is the retrieval half of P2 (tool retrieval, arXiv:2406.17465 /
    Online-Optimized RAG arXiv:2509.20415): rank tools by how well their
    description matches the task, so the right tool surfaces even with no
    learned history. BM25-lite: sum over task tokens of 1/log(1+df(tool set)).
    """
    docs = _tool_docs()
    task_tokens = set(_tokens(task))
    if not task_tokens or not docs:
        return {t: 0.0 for t in tools}
    corpus = [docs.get(t, "") for t in tools]
    import math

    scores: dict[str, float] = {}
    for tool in tools:
        doc = docs.get(tool, "")
        if not doc:
            scores[tool] = 0.0
            continue
        score = 0.0
        for tok in task_tokens:
            df = sum(1 for c in corpus if tok in c) or 1
            if tok in doc:
                score += 1.0 / math.log(2.0 + df)
        scores[tool] = score
    return scores


def rank(task: str, tools: list[str]) -> list[str]:
    """Reorder ``tools`` by retrieval (lexical) + learned per-shape + fitness."""
    try:
        tools = list(tools)
        if len(tools) <= 1:
            return tools
        weights = tool_weights(shape_of(task))
        genes = _fitness_genes()
        lex = lexical_scores(task, tools)
        order = {t: i for i, t in enumerate(tools)}  # stable tiebreak = input order
        return sorted(
            tools,
            key=lambda t: (
                -(
                    lex.get(t, 0.0)
                    + weights.get(t, 0.0) * 2.0
                    + float(genes.get(t, 0.0)) * 0.5
                ),
                order[t],
            ),
        )
    except Exception:  # noqa: BLE001
        return list(tools)


def shortlist(
    task: str, tools: list[str], k: int = 12, core: tuple[str, ...] = _CORE
) -> list[str]:
    """Adaptive depth (BoR): keep the top-k relevant tools + a safe core floor.

    No-ops when the set already fits. Never drops a core tool that is present.
    """
    try:
        tools = list(tools)
        if len(tools) <= k:
            return rank(task, tools)
        ranked = rank(task, tools)
        keep_core = [t for t in core if t in tools]
        rest = [t for t in ranked if t not in keep_core]
        limit = max(k, len(keep_core))
        return (keep_core + rest)[:limit]
    except Exception:  # noqa: BLE001
        return list(tools)


def enabled() -> bool:
    return os.environ.get("SWARM_TOOL_SHORTLIST", "").strip() == "1"
