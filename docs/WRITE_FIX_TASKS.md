# Write / fix task family (the north-star capability)

## The gap

Every existing family is **read/compute/git**. Nothing exercises **editing a file** —
yet "fix a real bug in V-Horseshoe" is the actual goal, and write/patch traces are the
most valuable GOLD for Experiment C (QLoRA). A coding CLI that never edits in its
curriculum never learns to edit.

## What's built — `qwen_train/fix_tasks.py` (+ 6 tests)

Deterministic, single-fix bugs, each with an exact verifier:

| bug | broken | fixed |
|---|---|---|
| off_by_one | `sum(range(1, n))` | `sum(range(1, n + 1))` |
| wrong_op | `a - b` | `a + b` |
| missing_return | `x * 2` | `return x * 2` |
| wrong_default | `factor=2` | `factor=3` |
| string_case | `s.lower()` | `s.upper()` |

Each task writes a `module.py` (broken by construction) + `check.py` into the sandbox,
and `verify_fix()` re-runs the check after the run: **exit 0 AND `check.py` byte-unchanged**
(sha256) — so editing the test to cheat is rejected.

## The safety model — three required parts

1. **Scratch sandbox** — `data/curriculum_fix/<id>/` (gitignored: `**/data/`).
2. **Path-scoped write** *(the gating enhancement — not yet applied)* — the write gate is
   currently **tool-level** (`filesystem` write = ALWAYS_CONFIRM, no path awareness), so a
   write grant would open the **whole repo**. Two changes are required together:
   - `swarm_os/lib/mcp/filesystem.py`: when `SWARM_WRITE_ROOT` is set, `write`/`patch`
     must resolve **under it** (default unset = today's behaviour).
   - `swarm_os/services/approval_registry.py`: add `filesystem` to the grantable set so a
     scoped, expiring grant can relax write — safe **only** because `SWARM_WRITE_ROOT`
     confines it to the sandbox.
3. **Post-run verify** — `verify_fix()` (built): run the check + hash the check file.

> Never grant broad filesystem write without the write-root. The two are one control.

## Runner integration (deferred until the 400 stops)

`run_curriculum.run_item` verifies the answer text via `verify()`, which has no
`fix_file` type. Two options:
- add a `fix_file` branch to `verify()` that calls `fix_tasks.verify_fix(item)`, or
- a dedicated `run_fix_tasks.py` harness (recommended — keeps the read-only verifier path
  untouched, and lets the fix runner set `SWARM_WRITE_ROOT`).

Either way the run must: set `SWARM_WRITE_ROOT=data/curriculum_fix`, grant `filesystem`,
run the CLI with the fix prompt, then `verify_fix()`.

## Why it matters

- **North star**: this is the family the whole project exists for.
- **Learning signal**: real tool choice (read the check, patch the module, run it) with a
  **low baseline** (broken by construction ≈ 0% before the fix) — maximum headroom.
- **Recovery**: naturally produces failure→recovery trajectories (edit → check fails →
  re-edit), i.e. real self-healing data.
- **GOLD for Experiment C**: the only family whose traces teach *editing*.
