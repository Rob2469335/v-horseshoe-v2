"""Durable agent-run checkpointing (2026 autonomy layer, move 3).

An interrupted step_agent_stream currently replays from the top — losing all of
L1-L6's verified state (read_paths, test_pass_result, _tests_ran, _contract_finals,
the fix-intent/internet guards, the fitness counters) and re-doing work an
interrupted run already finished. This module persists the run state at each turn
boundary so a crash resumes from the last consistent point instead of zero.

DESIGN (locked with the reviewer):
- One file per run (checkpoint_id), ATOMIC overwrite-latest: write .tmp then
  os.replace() so a mid-write crash leaves the previous valid checkpoint, never
  a torn file. No delta/reconstruction path.
- checkpoint_id = sha256(agent_id | canonicalize(prompt))[:16]. `prompt` is the
  ORIGINAL user-facing goal text. canonicalize = strip + collapse whitespace so
  a slightly different resume call can never silently fail to find its own
  checkpoint. Resume looks up by the STORED id — never re-derives the hash from
  caller-supplied text, so the whole 'silently starts fresh' class is removed
  by construction.
- DELETE only when the run actually finished: the final passed L1's contract
  check (handler_status == DONE). It must NOT delete on loop-exit, on any
  failed-exit path (LLM-abort, loop-abort, healing-fail, max-turns), or on an
  L1-rejected final (handler_status == CONTINUE) — those all still need the
  checkpoint to continue.
- The checkpoint is INVISIBLE to the watch-loop repair budget: it stores agent-
  run state only, never touches repair_breaker.json / auto_repairs.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from filelock import FileLock

log = logging.getLogger("Checkpoint")

_CHECKPOINT_DIR = Path("data/checkpoints")


def canonicalize(text: str) -> str:
    """Normalize a goal prompt so the checkpoint id is stable across slightly
    different spellings of the same logical goal (trailing whitespace, wrapping)."""
    return re.sub(r"\s+", " ", str(text or "").strip())


def checkpoint_id(agent_id: str, prompt: str) -> str:
    return hashlib.sha256(
        f"{agent_id}|{canonicalize(prompt)}".encode("utf-8")
    ).hexdigest()[:16]


def _checkpoint_path(cid: str) -> Path:
    return _CHECKPOINT_DIR / cid / "checkpoint.json"


def write_checkpoint(cid: str, payload: dict) -> None:
    """Atomic overwrite-latest. Never leaves a torn file: write .tmp, os.replace."""
    try:
        path = _checkpoint_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        lock = FileLock(str(path) + ".lock", timeout=5.0)
        with lock:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
    except Exception as exc:
        log.warning("Checkpoint write failed (%s): %s", cid, exc)


def load_checkpoint(cid: str) -> dict | None:
    """Return the persisted checkpoint or None (missing/corrupt = start fresh)."""
    try:
        path = _checkpoint_path(cid)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "turn" not in data:
            return None
        return data
    except Exception as exc:
        log.warning("Checkpoint load failed (%s): %s", cid, exc)
        return None


def delete_checkpoint(cid: str) -> None:
    """Remove the checkpoint. Called ONLY after a final was ACCEPTED by L1
    (handler_status == DONE) — never on a rejected/aborted/max-turns exit."""
    try:
        path = _checkpoint_path(cid)
        lock = FileLock(str(path) + ".lock", timeout=5.0)
        with lock:
            if path.exists():
                path.unlink()
        tmp = path.with_suffix(".json.tmp")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("Checkpoint delete failed (%s): %s", cid, exc)
