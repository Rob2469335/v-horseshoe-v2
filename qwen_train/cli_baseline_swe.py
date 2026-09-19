"""SWE-rebench observational baseline for the CLI."""

import argparse
import asyncio
import json
import os
import sys
import threading
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run_curriculum as rc
import swe_rebench_probe as probe

DEFAULT_POOL = _HERE / "curriculum" / "swe_pool.jsonl"
OUT = _HERE / "results" / "cli_baseline_swe.jsonl"

_write_lock = threading.Lock()
_abort_flag = False


def _backend_up(attempts: int = 3, timeout: int = 5) -> bool:
    """FAST liveness for the per-task loop: a TCP connect to :8000.

    Deliberately NOT an HTTP probe. `/readyz` and `/health` both probe
    llama.cpp and were measured at **30-45s under load** — a 5s HTTP timeout
    therefore could never pass and spuriously aborted a whole batch with 0 rows
    while the backend was perfectly healthy. A connect answers the decisive
    question ("is the process still listening?") instantly; the DEEP check
    (can the backend actually execute a tool?) stays in the preflight's
    /tools/execute round-trip, which runs once per batch.
    """
    import socket

    for attempt in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=timeout):
                return True
        except OSError as exc:
            print(f"  backend probe {attempt + 1}/{attempts} failed: {exc}")
            time.sleep(2)
    return False


def _probe_read(work: Path) -> tuple[bool, str]:
    """/tools/execute -> filesystem read of a canary inside WORK but outside the repo.

    Proves the backend's sandbox ROOT reaches the workspace. Returns (ok, detail).
    """
    canary = work / "_canary_workspace_root.txt"
    try:
        work.mkdir(parents=True, exist_ok=True)
        canary.write_text("canary", encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write canary {canary}: {exc}"

    body = json.dumps(
        {
            "capability": "filesystem",
            "payload": {"operation": "read", "path": str(canary)},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/tools/execute",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"/tools/execute call failed: {exc}"

    # The tool result is NESTED under `data` (`{"status":…, "data": {"ok": …}}`),
    # NOT top-level — checking d["ok"] silently failed every time.
    result = d.get("data") if isinstance(d.get("data"), dict) else d
    if result.get("ok"):
        return True, ""
    return False, str(result.get("error") or d)


def _status_sandbox() -> dict | None:
    """The backend's effective tool bounds from /status (None if unreported)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"PREFLIGHT: /status call failed: {exc}")
        return None
    sb = d.get("sandbox")
    return sb if isinstance(sb, dict) else None


def _preflight_workspace_root() -> bool:
    """Fail-closed that the BACKEND can READ **and WRITE** this workspace.

    The agent's TOOLS execute in the BACKEND process, not in this runner (the
    CLI only streams HTTP). So `SWARM_WORKSPACE_ROOT` must be set on the BACKEND
    at startup — setting it in this process's os.environ does NOT move the
    backend's sandbox boundary, and a batch against a mis-started backend
    produces a whole run of fake failures.

    A READ probe is NOT sufficient (2026-09-15): a backend with a workspace root
    but a RELATIVE `SWARM_WRITE_ROOT` (the ambient `data/curriculum_fix`)
    resolves that under the workspace and then refuses EVERY write, while reads
    work perfectly. That state produced a 14-task run with ZERO source edits and
    looked, row by row, like a capability failure. So the write bound is checked
    explicitly and aborts the batch.
    """
    work = probe.WORK.resolve()
    want = os.path.realpath(str(work))

    ok_read, detail = _probe_read(work)
    if not ok_read:
        print("PREFLIGHT FAILED — the backend cannot read inside the workspace:")
        print(f"  {detail}")
        print(f"  → RESTART THE BACKEND with SWARM_WORKSPACE_ROOT={probe.WORK}")
        print(
            "    (the tools run in the backend; this runner's env does not move its boundary)"
        )
        return False
    print(f"PREFLIGHT: backend reads the workspace ✓ ({probe.WORK})")

    sb = _status_sandbox()
    if sb is None:
        print(
            "PREFLIGHT FAILED — /status reported no `sandbox` bounds (stale backend?)."
        )
        print("  → restart the backend on this revision so /status exposes `sandbox`.")
        return False

    ws_root = sb.get("workspace_root")
    if not ws_root or os.path.realpath(str(ws_root)) != want:
        print("PREFLIGHT FAILED — the backend's workspace root is not this workspace:")
        print(f"  backend workspace_root: {ws_root}")
        print(f"  wanted:                 {probe.WORK}")
        return False

    if not sb.get("write_covers_workspace"):
        print("PREFLIGHT FAILED — the backend can READ the workspace but NOT WRITE it:")
        print(f"  workspace_root: {sb.get('workspace_root')}")
        print(f"  write_root:     {sb.get('write_root')}")
        print("  ❌ WRITE ROOT DOES NOT COVER THE WORKSPACE — ABORT")
        print(f"     restart the backend with SWARM_WRITE_ROOT={probe.WORK} as well.")
        return False

    print(
        "PREFLIGHT: backend writes the workspace ✓ "
        f"(write_root={sb.get('write_root') or 'unrestricted'})"
    )
    return True


def fetch_hf_instance(instance_id: str, split: str) -> dict:
    url = f"https://datasets-server.huggingface.co/rows?dataset=nebius/SWE-rebench-V2&config=default&split={split}"
    for p in range(10):
        try:
            with urllib.request.urlopen(
                f"{url}&offset={p * 100}&length=100", timeout=15
            ) as r:
                data = json.loads(r.read().decode("utf-8"))
                for row in data.get("rows", []):
                    if row["row"].get("instance_id") == instance_id:
                        return row["row"]
        except Exception as exc:  # noqa: BLE001
            print(f"  HF page {p} fetch failed: {exc}")
    raise ValueError(f"Instance {instance_id} not found in HF dataset split {split}")


def _failing_ids(output: str) -> set[str]:
    """Test ids reported FAILED/ERROR in the pytest -rA short summary."""
    ids: set[str] = set()
    for line in (output or "").splitlines():
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                # pytest -rA prints `FAILED <id> - <message>`. Split on ' - ',
                # NOT on ' ': a parametrized id can contain SPACES
                # (`...[unsupported Metadata-Version]`), and splitting on ' '
                # truncated it to `...[unsupported`, so it never matched the
                # F2P/P2P list and a pre-existing failure read as 0.
                ids.add(line[len(prefix) :].split(" - ")[0].strip())
    return ids


def _test_result(
    after_output: str,
    f2p: list[str],
    p2p: list[str],
    base_p2p_fail: set[str],
) -> tuple[bool, str]:
    """Verdict AFTER the agent, judged against the instance's BASE state.

    Requiring `p2p_f == 0` outright is WRONG: instances can carry PRE-EXISTING
    P2P failures (twine does — `test_pkginfo_returns_no_metadata[unsupported
    Metadata-Version]` fails at base), so that rule would fail every run
    regardless of what the CLI did. The correct criterion is: **all F2P pass,
    and NO NEW p2p failure** (i.e. none outside the base failure set).
    """
    failing = _failing_ids(after_output)
    f2p_p, _f2p_f = probe._f2p_result(after_output, f2p)
    # ENV vs CAPABILITY: no ids at all -> the suite did not run (collection
    # error / missing dep) -> ENV failure, not a CLI failure.
    import re
    has_summary = bool(re.search(r'={3,}.*\b(passed|failed|error|deselected)\b.*={3,}', after_output, re.IGNORECASE))
    if not has_summary and not failing:
        return False, "env_error"
    new_p2p = sorted({t for t in p2p if t in failing} - set(base_p2p_fail))
    if new_p2p:
        return False, f"regression: {len(new_p2p)} new p2p failure(s)"
    if f2p_p != len(f2p):
        return False, f"f2p: {f2p_p}/{len(f2p)} passed"
    return True, "passed"


def _reset_instance(inst: dict, hf_inst: dict) -> Path:
    base = inst["base_commit"]
    instance_id = inst["instance_id"]
    test_patch = hf_inst["test_patch"]

    d = probe.WORK / instance_id
    src = d / "repo"

    subprocess.run(["git", "reset", "--hard", base], cwd=str(src), capture_output=True)
    # `-fd` (NOT -fdx): -x also deletes ignored files, including the editable
    # install's *.egg-info — the same command the validated probe/pool builder use.
    subprocess.run(["git", "clean", "-fd"], cwd=str(src), capture_output=True)
    if test_patch:
        # OUTSIDE the agent's sandbox (probe._meta_dir), same reason as the gold
        # patch: anything under WORK is readable by the agent under measurement.
        patch_file = probe._meta_dir(instance_id) / "test.patch"
        patch_file.write_text(test_patch, encoding="utf-8")
        subprocess.run(
            ["git", "apply", str(patch_file)], cwd=str(src), capture_output=True
        )
    return src


def _clean_shadowing_metadata(src: Path) -> list:
    """Remove untracked `*.dist-info` dirs from the repo root, before pytest runs.

    Why this exists (measured 2026-09-15, deep-researched): the twine instance's
    OWN test suite CREATES a `twine-4.0.0.dist-info/` in the repo root as a
    fixture. pytest puts each module's directory at the FRONT of `sys.path`, and
    an editable install adds the source dir too, so
    `importlib_metadata.metadata("twine")` resolves that stub instead of the
    venv's real metadata. The stub carries only Metadata-Version/Name/Version, so
    `twine/__init__.py` raises `KeyError: 'summary'` while loading conftest — and
    EVERY test in the file errors. A CORRECT agent fix is then recorded as a
    failure, i.e. a broken environment written down as a capability result.

    Upstream confirmation: pypa/pip#7782 ("a non-editable dist shadows the
    editable"), pypa/setuptools#4170, pytest's pythonpath docs (rootdir first on
    sys.path). Docker-based SWE-bench harnesses never see this because each eval
    gets a fresh container; a local Docker-free harness has to clean.

    Only `*.dist-info` is removed. The editable install's `*.egg-info` is
    REQUIRED (that is why `_reset_instance` uses `git clean -fd`, not `-fdx`),
    and a test fixture is never an `egg-info`. Tracked dirs are never touched.
    """
    removed: list = []
    for p in sorted(src.glob("*.dist-info")):
        if not p.is_dir():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", p.name],
            cwd=str(src),
            capture_output=True,
        )
        if tracked.returncode == 0:
            continue  # part of the repo -> never delete
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p.name)
    return removed


def _run_tests(inst: dict) -> str:
    instance_id = inst["instance_id"]
    # The POOL record carries `test_cmd` at the TOP level - swe_pool.jsonl has no
    # `install_config`. Reading only install_config silently fell back to a bare
    # `pytest`, which ran the WHOLE suite (177 items, with a collection error)
    # instead of the instance's pinned command, so the F2P check was meaningless.
    test_cmd = str(
        inst.get("test_cmd") or (inst.get("install_config") or {}).get("test_cmd") or ""
    )
    d = probe.WORK / instance_id
    src = d / "repo"
    venv = d / "venv"
    py = venv / "Scripts" / "python.exe"

    # The BASE run can leave a shadowing stub behind for the AFTER run.
    stubs = _clean_shadowing_metadata(src)
    if stubs:
        print(f"  cleaned shadowing metadata stub(s): {', '.join(stubs)}")

    cmd = probe._test_cmd(py, test_cmd)
    rc, out = probe._run(cmd, src)
    return out


def _base_state(inst: dict) -> set[str]:
    """Failing test ids BEFORE the agent touches anything.

    The base run is what makes the P2P criterion meaningful: a P2P test that is
    already red at base is the instance's state, not a regression the CLI caused.
    """
    return _failing_ids(_run_tests(inst))


def _build_prompt(inst: dict, hf_inst: dict) -> str:
    """Name the workspace, or the agent has no anchor.

    Without this the CLI made exactly ONE tool call (`web_fetch`) and stopped —
    its own grounding (`AGENTS.md`/project map) lives in the REPO, which is now
    OUTSIDE the workspace sandbox, so the bare problem statement left it with
    nothing to aim at.
    """
    repo = (probe.WORK / inst["instance_id"] / "repo").resolve()
    ps = (hf_inst.get("problem_statement") or "").strip()
    test_cmd = inst.get("test_cmd") or "python -m pytest"
    # The tool contract is load-bearing, not boilerplate: the first 14-task batch
    # made ZERO source edits, and the CLI's own final diagnosis (twine, 2026-09-15)
    # named the cause — it reached for `sandbox_repl` to inspect files, whose
    # Security Gate blocks `open()`/`pathlib`, so every snippet was denied and it
    # looped until the turn budget was gone. Naming the read/write tool (and the
    # test command) removes the loop rather than blaming the model.
    return (
        f"Work inside this repository — your filesystem tools can read and write "
        f"under it:\n{repo}\n\n"
        f"Problem:\n{ps}\n\n"
        f"Fix the problem in that repository so the failing tests pass.\n\n"
        f"TOOL CONTRACT — follow this or you will loop and waste the turn budget:\n"
        f"- You have a SHELL: `sandbox_repl` with language=\"bash\" and a `command`. "
        f"Use it to look around and to verify (e.g. `git diff`, `ls`, "
        f"`python -m pytest -q tests/test_x.py`).\n"
        f"- EDIT files with the `filesystem` tool — operation=patch with `old` = the "
        f"EXACT existing text and `new` = the replacement (read the file first so "
        f"`old` matches exactly), or operation=write for a whole file.\n"
        f"- Do NOT use `sandbox_repl` language=\"python\" to read files — its "
        f"Security Gate blocks `open()`/`pathlib`. Use the shell or `filesystem`.\n"
        f"- The tests that must pass are run by:\n  {test_cmd}\n"
        f"- Do NOT modify test files."
    )


def _is_infra_failure(timed_out: bool, cli_ok: bool, tool_order: list) -> bool:
    """True when a run failed for INFRASTRUCTURE reasons, not capability.

    Fail-closed in the direction that matters. A timed-out run, or a CLI that
    produced neither tool activity nor content, says nothing about whether the
    agent could do the task — only that it never got to try (2026-09-15: two
    600s timeouts were each the FIRST run after a backend restart, while the warm
    run next to them finished in 227s). Recording those as capability failures is
    precisely how a broken environment gets written down as a model weakness —
    and then trained on.
    """
    if timed_out:
        return True
    if cli_ok:
        return False
    # cli_ok is False: it is only an infra failure if the CLI never acted.
    return not tool_order


def _row_infra(r: dict) -> bool:
    """Infra-failure test for a RESULT ROW (re-derives for legacy rows)."""
    if "infra_failure" in r:
        return bool(r["infra_failure"])
    return _is_infra_failure(
        bool(r.get("timed_out")),
        bool(r.get("cli_ok")),
        r.get("first_tool_order") or [],
    )


def _summarize(rows: list) -> dict:
    """Pass rate over VALID measurements only.

    Infra failures are counted separately and EXCLUDED from the rate: a run that
    timed out never attempted the task, so folding it in would report an
    environmental stall as a capability failure.
    """
    valid = [r for r in rows if not _row_infra(r)]
    solved = [r for r in valid if r.get("first_attempt_success")]
    return {
        "total": len(rows),
        "infra_failures_excluded": len(rows) - len(valid),
        "measured": len(valid),
        "solved": len(solved),
        "capability_pass_rate": (round(len(solved) / len(valid), 3) if valid else None),
    }


async def process_task(
    inst: dict, sem: asyncio.Semaphore, args: argparse.Namespace
) -> dict:
    global _abort_flag
    async with sem:
        if _abort_flag:
            return {}
        if not _backend_up():
            print("BACKEND DOWN - setting abort flag")
            _abort_flag = True
            return {}

        instance_id = inst["instance_id"]
        print(f"Starting {instance_id}")

        hf_inst = await asyncio.to_thread(fetch_hf_instance, instance_id, "train")

        src = await asyncio.to_thread(_reset_instance, inst, hf_inst)
        _clean_shadowing_metadata(src)

        f2p = probe._parse_list_field(
            inst.get("fail_to_pass") or inst.get("FAIL_TO_PASS")
        )
        p2p = probe._parse_list_field(
            inst.get("pass_to_pass") or inst.get("PASS_TO_PASS")
        )

        # BASE baseline BEFORE the agent runs — what is red is the instance's
        # state, not something the CLI caused.
        base_failing = await asyncio.to_thread(_base_state, inst)
        base_p2p_fail = {t for t in p2p if t in base_failing}
        print(
            f"  base: {len([t for t in f2p if t in base_failing])}/{len(f2p)} f2p failing, "
            f"{len(base_p2p_fail)} pre-existing p2p failure(s)"
        )

        item = {
            "id": instance_id,
            "prompt": _build_prompt(inst, hf_inst),
            "split": inst.get("split", "train"),
        }

        res = await asyncio.to_thread(
            rc._attempt_once, item, args.timeout, allow_approval=True, record=False
        )

        out = await asyncio.to_thread(_run_tests, inst)
        after_failing = _failing_ids(out)

        ok, reason = _test_result(out, f2p, p2p, base_p2p_fail)

        # INFRA vs CAPABILITY (2026-09-15): a timed-out / no-activity run must
        # never be recorded as a capability failure — see _is_infra_failure.
        infra = _is_infra_failure(
            bool(res.get("timed_out")),
            bool(res.get("cli_ok")),
            res.get("tool_order") or [],
        )
        cat = "cli_error" if not res.get("cli_ok") else reason

        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": instance_id,
            "tests_visible": True,
            "first_attempt_success": ok,
            "first_failure_category": cat,
            "infra_failure": infra,
            "measurement_valid": not infra,
            "first_tool_order": res.get("tool_order", []),
            "first_elapsed_s": res.get("elapsed_s"),
            # The BASE/AFTER failure sets are what make the verdict auditable:
            # `base_p2p_fail` is the pre-existing red set, so a "regression"
            # verdict is provable rather than inferred.
            # WHY it did/didn't act — the CLI's own final content plus the
            # control-plane outcomes. Without this the row can only say THAT it
            # failed, never WHY (the 14-task batch showed 0 source edits and we
            # could not tell "chose not to act" from "was refused").
            "cli_ok": res.get("cli_ok"),
            "content": res.get("content"),
            "denied": res.get("denied"),
            "verify_reason": res.get("verify_reason"),
            "timed_out": res.get("timed_out"),
            "base_failing": sorted(base_failing),
            "after_failing": sorted(after_failing),
            "base_p2p_fail": sorted(base_p2p_fail),
            "f2p": f2p,
            "p2p": p2p,
            # Keep the tail (the pytest short summary is what proves the verdict);
            # the full output can be megabytes and would bloat the results JSONL.
            "test_output": out[-4000:],
        }

        with _write_lock:
            with open(args.out, "a") as f:
                f.write(json.dumps(row) + "\n")

        print(f"Finished {instance_id}: {cat}")
        return row


async def async_main(args):
    pool = Path(args.pool)
    cands = [
        json.loads(line)
        for line in pool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][: args.n]

    if not _backend_up():
        print("BACKEND DOWN - start stack")
        return 2

    if not _preflight_workspace_root():
        print(
            "ABORTING before any task: a mis-started backend would fake the whole batch."
        )
        return 3

    try:
        from swarm_os.services.trust_ledger import grant

        for scope in ("filesystem", "sandbox_repl"):
            grant(scope, 12 * 3600)
    except Exception as exc:  # noqa: BLE001
        print(f"  trust grant failed (gated tools will prompt): {exc}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(2)
    tasks = [process_task(c, sem, args) for c in cands]
    await asyncio.gather(*tasks)

    # INFRA vs CAPABILITY: report the rate over VALID measurements only, and
    # state the excluded count explicitly — a silently-dropped infra failure is
    # as misleading as one counted as a capability failure.
    try:
        rows = [
            json.loads(line)
            for line in Path(args.out).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError as exc:
        print(f"SUMMARY: could not read {args.out}: {exc}")
        return 2 if _abort_flag else 0

    summary = _summarize(rows)
    rate = summary["capability_pass_rate"]
    print(
        f"SUMMARY: measured={summary['measured']} "
        f"solved={summary['solved']} "
        f"pass_rate={rate if rate is not None else 'n/a'} "
        f"(infra_excluded={summary['infra_failures_excluded']})"
    )
    if summary["infra_failures_excluded"]:
        print("  (excluded rows never attempted the task — cold backend/timeout)")
    try:
        from _atomic import atomic_write_json

        side = Path(args.out).with_suffix(".summary.json")
        atomic_write_json(side, summary)
        print(f"  wrote {side}")
    except Exception as exc:  # noqa: BLE001
        print(f"  summary sidecar failed: {exc}")
    return 2 if _abort_flag else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(DEFAULT_POOL))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    os.environ["SWARM_NO_TOASTS"] = "1"
    os.environ["SWARM_MEMORY_INJECT"] = "0"
    os.environ["SWARM_EVOLUTION"] = "0"
    os.environ["SWARM_WORKSPACE_ROOT"] = str(probe.WORK)

    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()

