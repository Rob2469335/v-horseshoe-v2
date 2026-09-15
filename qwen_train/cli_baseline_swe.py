"""SWE-rebench observational baseline for the CLI."""
import argparse
import asyncio
import json
import os
import sys
import threading
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path: sys.path.insert(0, str(_HERE))

import run_curriculum as rc
import swe_rebench_probe as probe

DEFAULT_POOL = _HERE / "curriculum" / "swe_pool.jsonl"
OUT = _HERE / "results" / "cli_baseline_swe.jsonl"

_write_lock = threading.Lock()
_abort_flag = False

def _backend_up() -> bool:
    for path in ("/readyz", "/health"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as r:
                return r.status == 200
        except Exception:
            pass
    return False

def _preflight_workspace_root() -> bool:
    """Fail-closed check that the BACKEND can reach the workspace.

    The agent's TOOLS execute in the BACKEND process, not in this runner (the
    CLI only streams HTTP). So `SWARM_WORKSPACE_ROOT` must be set on the BACKEND
    at startup — setting it in this process's os.environ does NOT move the
    backend's sandbox boundary, and a batch run against a mis-started backend
    produces a whole run of fake failures.

    Probes the real path: /tools/execute -> filesystem read of a canary that
    lives inside WORK but OUTSIDE the repo. If that is refused, the backend's
    root is still the project root and we abort.
    """
    canary = probe.WORK / "_canary_workspace_root.txt"
    try:
        probe.WORK.mkdir(parents=True, exist_ok=True)
        canary.write_text("canary", encoding="utf-8")
    except OSError as exc:
        print(f"PREFLIGHT: cannot write canary {canary}: {exc}")
        return False

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
        print(f"PREFLIGHT: /tools/execute call failed: {exc}")
        return False

    # The tool result is NESTED under `data` (`{"status":…, "data": {"ok": …}}`),
    # NOT top-level — checking d["ok"] silently failed every time.
    result = d.get("data") if isinstance(d.get("data"), dict) else d
    if result.get("ok"):
        print(f"PREFLIGHT: backend reaches the workspace ✓ ({probe.WORK})")
        return True

    print("PREFLIGHT FAILED — the backend cannot read inside the workspace:")
    print(f"  {result.get('error') or d}")
    print(f"  → RESTART THE BACKEND with SWARM_WORKSPACE_ROOT={probe.WORK}")
    print("    (the tools run in the backend; this runner's env does not move its boundary)")
    return False


def fetch_hf_instance(instance_id: str, split: str) -> dict:
    url = f"https://datasets-server.huggingface.co/rows?dataset=nebius/SWE-rebench-V2&config=default&split={split}"
    for p in range(10):
        try:
            with urllib.request.urlopen(f"{url}&offset={p*100}&length=100", timeout=15) as r:
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
                ids.add(line[len(prefix) :].split(" ")[0].strip())
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
    if not failing:
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
        patch_file = d / "test.patch"
        patch_file.write_text(test_patch, encoding="utf-8")
        subprocess.run(["git", "apply", str(patch_file)], cwd=str(src), capture_output=True)
    return src

def _run_tests(inst: dict) -> str:
    instance_id = inst["instance_id"]
    # The POOL record carries `test_cmd` at the TOP level — swe_pool.jsonl has no
    # `install_config`. Reading only install_config silently fell back to a bare
    # `pytest`, which ran the WHOLE suite (177 items, with a collection error)
    # instead of the instance's pinned command, so the F2P check was meaningless.
    test_cmd = str(
        inst.get("test_cmd")
        or (inst.get("install_config") or {}).get("test_cmd")
        or ""
    )
    d = probe.WORK / instance_id
    src = d / "repo"
    venv = d / "venv"
    py = venv / "Scripts" / "python.exe"
    
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
    return (
        f"Work inside this repository — your filesystem tools can read and write "
        f"under it:\n{repo}\n\n"
        f"Problem:\n{ps}\n\n"
        f"Fix the problem in that repository so the failing tests pass. Read the "
        f"relevant files first, make the smallest correct change, then run the "
        f"tests to verify. Do NOT modify the test files."
    )

async def process_task(inst: dict, sem: asyncio.Semaphore, args: argparse.Namespace) -> dict:
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

        await asyncio.to_thread(_reset_instance, inst, hf_inst)

        f2p = probe._parse_list_field(inst.get("fail_to_pass") or inst.get("FAIL_TO_PASS"))
        p2p = probe._parse_list_field(inst.get("pass_to_pass") or inst.get("PASS_TO_PASS"))

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

        res = await asyncio.to_thread(rc._attempt_once, item, args.timeout, allow_approval=True, record=False)

        out = await asyncio.to_thread(_run_tests, inst)
        after_failing = _failing_ids(out)

        ok, reason = _test_result(out, f2p, p2p, base_p2p_fail)

        cat = "cli_error" if not res.get("cli_ok") else reason

        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": instance_id,
            "tests_visible": True,
            "first_attempt_success": ok,
            "first_failure_category": cat,
            "first_tool_order": res.get("tool_order", []),
            "first_elapsed_s": res.get("elapsed_s"),
            # The BASE/AFTER failure sets are what make the verdict auditable:
            # `base_p2p_fail` is the pre-existing red set, so a "regression"
            # verdict is provable rather than inferred.
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
    cands = [json.loads(line) for line in pool.read_text(encoding="utf-8").splitlines() if line.strip()][:args.n]
    
    if not _backend_up():
        print("BACKEND DOWN - start stack")
        return 2

    if not _preflight_workspace_root():
        print("ABORTING before any task: a mis-started backend would fake the whole batch.")
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
