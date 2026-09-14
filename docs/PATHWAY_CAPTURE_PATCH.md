# Pathway capture — ready-to-apply patch (UNAPPLIED)

**Status: prepared, NOT applied.** Apply only after the 400-run stops (editing
`runtime_v2/` during a curriculum run drifts mined line-count/symbol answers), then
restart the backend.

Implements the RETRIEVE event of `docs/PATHWAY_EVIDENCE.md`. Two runtime files + one test.

---

## Change 1 — `runtime_v2/services/stream_runner.py`

### 1a. thread `run_id` into the decision call (signature, line ~390)

```python
async def get_tool_decision(
    model: str,
    messages: list,
    agent_id: str,
    allowed_tools: list = None,
    run_id: str = "",
) -> Optional[dict]:
```

### 1b. add two module-level helpers (near the other helpers, e.g. after `_store_decision_reflexion`)

```python
_PATHWAY_TOOLS = (
    "filesystem", "sandbox_repl", "lsp", "git", "mcp", "web_search",
    "web_fetch", "semantic_search", "system", "screen", "playwright", "email",
)


def _warned_signatures(hint: str) -> list[str]:
    """Coarse `tool:err_class` keys a [PAST-MISTAKE WARNING] warns about.

    Matched by qwen_train/pathway.py against observed step failures. Coarse on
    purpose — see docs/PATHWAY_EVIDENCE.md (honest limits).
    """
    h = (hint or "").lower()
    classes = []
    if "not found" in h or "no such file" in h:
        classes.append("not_found")
    if "timeout" in h or "timed out" in h:
        classes.append("timeout")
    if "permission" in h or "denied" in h:
        classes.append("permission")
    if "malformed" in h or "json" in h or "parse" in h:
        classes.append("malformed")
    tools = [t for t in _PATHWAY_TOOLS if t in h] or ["unknown"]
    return sorted({f"{t}:{c}" for t in tools for c in (classes or ["other"])})


def _write_pathway(run_id: str, hint: str) -> None:
    """Append the RETRIEVE event to the run's trajectory (best-effort)."""
    if not run_id:
        return
    try:
        from pathlib import Path

        d = Path("data/trajectories")
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "record_type": "pathway",
            "run_id": run_id,
            "phase": "retrieve",
            "injected": True,
            "injected_chars": len(hint or ""),
            # lesson ids/scores need a structured return from check_for_past_mistakes
            # (follow-up); the correction text is enough for coherence metrics now.
            "lessons": [{"correction": (hint or "")[:400]}],
            "warned_signatures": _warned_signatures(hint),
        }
        with open(d / f"{run_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as pw_err:  # noqa: BLE001
        log.debug("pathway write skipped for run %s: %s", run_id, pw_err)
```

### 1c. write the event at the injection site (line ~609, inside `if hint and len(hint) > 10:`)

```python
                        injected_chars += len(hint)
                        _write_pathway(run_id, hint)          # <-- ADD
                        log.debug(
```

---

## Change 2 — `runtime_v2/api/agent_service_v2.py`

Pass the run id through (line ~1235):

```python
                decision = await get_tool_decision(
                    model,
                    messages,
                    agent_id,
                    allowed_tools=allowed_tools,
                    run_id=getattr(state, "run_id", ""),   # <-- ADD
                )
```

---

## Change 3 — test `tests/test_pathway_capture.py` (new)

- `_warned_signatures("File not found: use the filesystem tool")` →
  `["filesystem:not_found"]`.
- `_warned_signatures("timeout in sandbox_repl")` → `["sandbox_repl:timeout"]`.
- `_write_pathway("run-x", "File not found …")` writes one `pathway` line with
  `record_type=="pathway"`, `phase=="retrieve"`, `injected is True`, non-empty
  `warned_signatures` (monkeypatch `data/trajectories` via cwd or accept real dir + cleanup).
- `_write_pathway("", …)` is a no-op.
- **Seam:** drive `get_tool_decision` with a monkeypatched
  `get_reflection_service().check_for_past_mistakes` returning a hint, assert a
  `pathway` record is written for the passed `run_id` (proves the call site fires).

---

## Apply order

1. Confirm the 400 runner is gone: `Get-CimInstance Win32_Process | ? CommandLine -like '*run_curriculum*'` → 0.
2. Apply Change 1, 2, 3.
3. `.venv\Scripts\python.exe -m pytest tests/test_pathway.py tests/test_pathway_capture.py -q` → green.
4. `ruff check runtime_v2/services/stream_runner.py runtime_v2/api/agent_service_v2.py tests/test_pathway_capture.py --select E9,F` → clean.
5. Restart the backend (user).
6. One live run; then `.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'qwen_train');import pathway;print(pathway.metrics_from_dir('data/trajectories'))"`
   → `retrieval_rate > 0` proves the pathway capture is live.
7. Commit: `SERVICE: pathway capture (RETRIEVE event) into trajectories`.

## Known follow-ups (NOT in this patch)

- **lesson ids/scores**: `check_for_past_mistakes()` returns a formatted string;
  capturing `lesson_id`/`score` needs it to also expose the retrieved points
  (separate change to `swarm_os/services/reflection_loop.py`).
- **real model in provenance**: step `model_name` currently records the requested
  alias (`robs4b`), not the actual inference model (`deepseek/deepseek-v4-flash`).
  Thread the resolved model from the decision path into `_write_run_step`.
