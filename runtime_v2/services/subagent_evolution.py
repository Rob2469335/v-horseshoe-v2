"""Evolvable config for ``mutable: true`` subagents (opt-in, staged, human-approved).

A mutable `.rob/agents/<name>.md` subagent's config — tool allowlist, model, and
context budget — is treated as a **genome**. Candidates are scored with the SAME
shared outcome signal the tool-policy kernel uses
(:func:`swarm_os.services.outcome_fitness.best_aggregate_fitness`) and *staged*,
never auto-applied: a human promotes via :func:`promote` / reverts via
:func:`rollback`, and every promotion writes an ``[AUTO-REPAIR]``-format line to
the AGENTS.md changelog through the shared :func:`watch_loop._audit_write`.

Safety contract (audited 2026-09-13):
  - **Default OFF** (``SWARM_SUBAGENT_EVOLUTION`` unset): :func:`propose` is a
    no-op. Nothing runs without the explicit flag.
  - **Only ``mutable: true``** subagents are ever considered; ``mutable: false``
    or an absent key is provably inert (never proposed, never mutated, never
    written).
  - **Capability surface only.** This module rewrites *only* the target agent's
    ``tools`` / ``model`` / ``budget`` frontmatter — never a trust-boundary file
    (``hooks.json``, permissions, approval tiers) and never the subagent's body.
  - **Reversible.** Promotion backs the file up (``<name>.md.bak``); rollback
    restores it byte-for-byte.

Scoring honesty: today :func:`score` returns the shared aggregate outcome
signal (the same one the tool-policy population uses, per the documented
`best_aggregate_fitness` fallback). Per-config fitness — tagging a run with the
subagent config hash so the score is config-specific — is the follow-up; until
then the score is a shared proxy, not a per-genome measurement.
"""

from __future__ import annotations

import copy
import json
import os
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

import logging

from runtime_v2.services import subagent_registry as reg

log = logging.getLogger(__name__)

STAGED_DIR = Path("data/evolution/subagent_staged")
_MODEL_CANDIDATES = ("robs4b",)
_BUDGET_MIN = 1024
_BUDGET_MAX = 16384


def _enabled() -> bool:
    return os.environ.get("SWARM_SUBAGENT_EVOLUTION", "").strip() == "1"


def enabled() -> bool:
    """Public opt-in check (the flag defaults OFF)."""
    return _enabled()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agents_root() -> Path:
    return Path(reg._AGENTS_ROOT)


def _md_path(name: str) -> Path:
    if not reg._safe_name(name):
        raise ValueError(f"unsafe subagent name: {name!r}")
    return _agents_root() / f"{name}.md"


def mutable_subagents() -> list[dict]:
    """File-based subagents explicitly marked ``mutable: true``.

    Names that shadow a built-in are excluded: the runtime always uses the
    built-in (built-ins win), so evolving a shadow file would record a change
    that never takes effect (a dishonest audit trail).
    """
    builtins = _builtin_names()
    return [
        s
        for s in reg.list_subagents()
        if s.get("mutable") and s.get("name") not in builtins
    ]


def _builtin_names() -> set[str]:
    """Names of the built-in agents (from the runtime tool table)."""
    try:
        from runtime_v2.prompts.system_prompts import _AGENT_TOOLS

        return set(_AGENT_TOOLS)
    except Exception:  # noqa: BLE001
        return set()


def config_genome(sub: dict) -> dict:
    """Extract the evolvable config genome from a subagent descriptor."""
    return {
        "tools": list(sub.get("tools") or ["final", "filesystem"]),
        "model": sub.get("model") or "robs4b",
        "budget": int(sub.get("budget") or 4096),
    }


def _tool_pool() -> list[str]:
    """Candidate tools to mutate within: the union of built-in agent tools."""
    try:
        from runtime_v2.prompts.system_prompts import _AGENT_TOOLS

        pool: set[str] = set()
        for tools in _AGENT_TOOLS.values():
            pool.update(tools)
        return sorted(pool)
    except Exception:  # noqa: BLE001
        return ["filesystem", "final"]


def mutate(genome: dict, rng: random.Random | None = None) -> dict:
    """Return a copy of ``genome`` with exactly ONE changeable gene mutated."""
    rng = rng or random.Random()
    out = copy.deepcopy(genome)
    ops: list[str] = []
    # tools is effectively always changeable (the pool is large)
    if len(out.get("tools", [])) > 1 or set(_tool_pool()) - set(out.get("tools", [])):
        ops.append("tools")
    ops.append("budget")
    if len(_MODEL_CANDIDATES) > 1:
        ops.append("model")
    gene = rng.choice(ops)

    if gene == "tools":
        tools = set(out["tools"])
        pool = [t for t in _tool_pool() if t not in tools]
        if pool and (rng.random() < 0.5 or len(tools) <= 1):
            tools.add(rng.choice(pool))
        elif len(tools) > 1:
            tools.discard(rng.choice(sorted(tools)))
        out["tools"] = sorted(tools)
    elif gene == "budget":
        factor = 1.25 if rng.random() < 0.5 else 0.8
        out["budget"] = max(_BUDGET_MIN, min(_BUDGET_MAX, int(out["budget"] * factor)))
    elif gene == "model":
        out["model"] = rng.choice(_MODEL_CANDIDATES)
    return out


def score(genome: dict) -> float:
    """Shared outcome-fitness signal for a config (see module docstring)."""
    try:
        from swarm_os.services.outcome_fitness import best_aggregate_fitness

        return float(best_aggregate_fitness() or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _stage_path(name: str, genome: dict) -> Path:
    h = abs(hash(json.dumps(genome, sort_keys=True))) % (10**10)
    return STAGED_DIR / f"{name}_{h}.json"


def propose(name: str, rng: random.Random | None = None) -> dict | None:
    """Stage a mutated config for a mutable subagent. Never touches the file.

    Returns the staged record, or None when evolution is disabled / the agent
    is absent or not mutable.
    """
    if not _enabled():
        return None
    sub = reg.get_subagent(name)
    if not sub or not sub.get("mutable") or name in _builtin_names():
        return None
    current = config_genome(sub)
    cur_score = score(current)
    if cur_score <= 0.0:
        # Anti-fabrication (arXiv:2607.13083 "Phantom Guardrails"): never stage
        # a mutation with no real outcome signal — self-improving systems
        # otherwise "fix" failures that never happened.
        log.debug("subagent_evolution: no outcome signal for %s; not proposing", name)
        return None
    candidate = mutate(current, rng)
    cand_score = score(candidate)
    if cand_score < cur_score:
        # Regression-risk gate (SAHOO, arXiv:2603.06333): don't stage a config
        # that measures worse than the incumbent.
        log.debug("subagent_evolution: candidate for %s scores worse; skipped", name)
        return None
    record = {
        "ts": _now(),
        "agent": name,
        "current": current,
        "candidate": candidate,
        "current_score": round(cur_score, 4),
        "candidate_score": round(cand_score, 4),
    }
    try:
        STAGED_DIR.mkdir(parents=True, exist_ok=True)
        _stage_path(name, candidate).write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("subagent_evolution: staging failed for %s: %s", name, exc)
        return None
    return record


def list_staged(name: str | None = None) -> list[dict]:
    """Staged proposals for ``name`` (or all), newest first."""
    if not STAGED_DIR.exists():
        return []
    out: list[dict] = []
    for p in STAGED_DIR.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict):
            continue  # a stray non-record file must not crash the surface
        if name is None or rec.get("agent") == name:
            rec["_path"] = str(p)
            out.append(rec)
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out


def _rewrite_frontmatter(text: str, updates: dict) -> str:
    """Replace/add ``key: value`` lines in a frontmatter block, keep the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    fm = lines[1:end]
    seen: set[str] = set()
    for i, raw in enumerate(fm):
        if ":" in raw:
            key = raw.split(":", 1)[0].strip().lower()
            if key in updates:
                fm[i] = f"{key}: {updates[key]}"
                seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            fm.append(f"{key}: {value}")
    return "\n".join([lines[0], *fm, *lines[end:]])


def validate_candidate(name: str, candidate: dict) -> tuple[bool, str]:
    """Machine-checkable acceptance invariants for a config candidate.

    Falsifiable release gates (arXiv:2607.13070): the standing invariants that
    must hold for ANY promoted config — mutable-and-file-based, a non-empty
    known tool allowlist, an in-range budget, a non-empty model string.
    """
    sub = reg.get_subagent(name)
    if not sub or not sub.get("mutable"):
        return False, f"{name!r} is not a mutable subagent"
    if name in _builtin_names():
        return False, f"{name!r} shadows a built-in agent (never evolved)"
    tools = candidate.get("tools") or []
    if not tools:
        return False, "candidate has no tools"
    unknown = [t for t in tools if t not in set(_tool_pool())]
    if unknown:
        return False, f"unknown tool(s): {', '.join(map(str, unknown))}"
    budget = candidate.get("budget")
    if not isinstance(budget, int) or not (_BUDGET_MIN <= budget <= _BUDGET_MAX):
        return False, f"budget {budget} out of range [{_BUDGET_MIN}, {_BUDGET_MAX}]"
    model = candidate.get("model")
    if not isinstance(model, str) or not model.strip():
        return False, "model must be a non-empty string"
    return True, "ok"


def promote(name: str, reviewer: str = "") -> dict:
    """Apply the newest staged config for ``name`` (human-gated, reversible).

    Runs the machine-checkable acceptance invariants before touching anything;
    a violation refuses the promotion. Records the reviewer in the audit trail.
    """
    staged = list_staged(name)
    if not staged:
        return {"ok": False, "reason": f"no staged config for {name!r}"}
    rec = staged[0]
    ok, reason = validate_candidate(name, rec.get("candidate") or {})
    if not ok:
        return {"ok": False, "reason": f"acceptance gate failed: {reason}"}
    md = _md_path(name)
    if not md.exists():
        return {"ok": False, "reason": f"{md} not found"}
    candidate = rec["candidate"]
    updates = {
        "tools": ", ".join(candidate["tools"]),
        "model": candidate["model"],
        "budget": candidate["budget"],
    }
    try:
        bak = md.with_suffix(".md.bak")
        if not bak.exists():
            # Keep the ORIGINAL as the backup across repeated promotions, so a
            # single rollback always restores the pre-evolution state (the old
            # unconditional copy2 overwrote it with the intermediate state).
            shutil.copy2(md, bak)
        md.write_text(
            _rewrite_frontmatter(md.read_text(encoding="utf-8"), updates),
            encoding="utf-8",
        )
        reg._CACHE = (0.0, [])  # invalidate the registry cache
    except OSError as exc:
        return {"ok": False, "reason": f"write failed: {exc}"}

    try:
        from swarm_os.services.watch_loop import _audit_write

        approved = f" [approved by {reviewer}]" if reviewer else ""
        line = (
            f"- **[SUBAGENT-CONFIG] ({_now()})**: {name} — tools/model/budget "
            f"evolved (score {rec.get('current_score')}→{rec.get('candidate_score')})"
            f"{approved}\n"
        )
        _audit_write(
            {
                "ts": _now(),
                "type": "SUBAGENT-CONFIG",
                "agent": name,
                "reviewer": reviewer,
                "from": rec.get("current"),
                "to": candidate,
                "trigger": "subagent_evolution",
            },
            line,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("subagent_evolution: audit write failed: %s", exc)

    return {"ok": True, "agent": name, "applied": updates}


def reject(name: str, reviewer: str = "") -> dict:
    """Discard every staged proposal for ``name`` (deletes staged files)."""
    staged = list_staged(name)
    if not staged:
        return {"ok": False, "reason": f"no staged config for {name!r}"}
    removed = 0
    for rec in staged:
        try:
            Path(rec["_path"]).unlink()
            removed += 1
        except OSError:
            continue
    try:
        from swarm_os.services.watch_loop import _audit_write

        by = f" [by {reviewer}]" if reviewer else ""
        _audit_write(
            {
                "ts": _now(),
                "type": "SUBAGENT-CONFIG-REJECTED",
                "agent": name,
                "reviewer": reviewer,
                "removed": removed,
                "trigger": "subagent_evolution",
            },
            f"- **[SUBAGENT-CONFIG-REJECTED] ({_now()})**: {name} — "
            f"{removed} proposal(s) discarded{by}\n",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("subagent_evolution: audit write failed: %s", exc)
    return {"ok": True, "agent": name, "removed": removed}


def rollback(name: str) -> dict:
    """Restore a promoted subagent config from its ``.bak`` backup."""
    if not reg.get_subagent(name):
        return {"ok": False, "reason": f"{name!r} is not a known file-based subagent"}
    md = _md_path(name)
    bak = md.with_suffix(".md.bak")
    if not bak.exists():
        return {"ok": False, "reason": f"no backup for {name!r}"}
    try:
        shutil.copy2(bak, md)
        reg._CACHE = (0.0, [])
    except OSError as exc:
        return {"ok": False, "reason": f"restore failed: {exc}"}
    return {"ok": True, "agent": name}
