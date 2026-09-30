"""Launch the Twine evaluation through run_repair_task.py's infrastructure."""
import json
import sys
import os
from pathlib import Path

ROOT = Path(r"C:\Users\rober\Projects\v-horseshoe-v2")
sys.path.insert(0, str(ROOT / "qwen_train"))

ps_file = Path(r"C:\Users\rober\AppData\Local\Temp\opencode\twine_ps.txt")
tp_file = Path(r"C:\Users\rober\Projects\swe_probe_meta\pypa__twine-1066\test.patch")

instance_id = "pypa__twine-1066"
base_commit = "4a1fc064a7899872ee845df6a8810bb51a6845ac"
test_cmd = "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider -W ignore::DeprecationWarning tests/test_package.py"
f2p = [
    "tests/test_package.py::test_metadata_dictionary_keys",
    "tests/test_package.py::test_metadata_dictionary_values[None]",
    "tests/test_package.py::test_metadata_dictionary_values[gpg_signature1]",
]

problem_statement = ps_file.read_text(encoding="utf-8")
test_patch = tp_file.read_text(encoding="utf-8")

import run_curriculum as rc
import cli_baseline_swe as cls
import swe_rebench_probe as probe

WORK = probe.WORK
inst = {
    "instance_id": instance_id,
    "base_commit": base_commit,
    "test_cmd": test_cmd,
    "fail_to_pass": f2p,
    "pass_to_pass": [],
    "split": "repair",
}
hf_inst = {"problem_statement": problem_statement, "test_patch": test_patch}

print("=" * 60)
print("STEP 1: Reset repo to base + apply test_patch")
print("=" * 60)
src = cls._reset_instance(inst, hf_inst)
cls._clean_shadowing_metadata(src)
print(f"  repo: {src}")
print(f"  HEAD: {probe._run(['git', 'rev-parse', 'HEAD'], src)[1].strip()}")

print()
print("=" * 60)
print("STEP 2: Verify baseline FAILS (test_patch applied, source buggy)")
print("=" * 60)
from swe_rebench_probe import _run, _test_cmd, _f2p_result
py = src / ".venv" / "Scripts" / "python.exe"
cmd = _test_cmd(py, test_cmd)
rc_code, baseline_output = _run(cmd, src, timeout=120)
print(f"  pytest exit code: {rc_code}")
f2p_p, f2p_f = _f2p_result(baseline_output, f2p)
print(f"  F2P: {f2p_p} passed, {f2p_f} failed (expected: 0 passed, 3 failed)")
if f2p_f < 3:
    print("  ERROR: baseline did not fail as expected!")
    print(baseline_output[-2000:])
    sys.exit(1)
print("  BASELINE CONFIRMED: 3/3 F2P tests FAIL at base commit")

print()
print("=" * 60)
print("STEP 3: Run robs4b repair agent")
print("=" * 60)
os.environ["SWARM_WORKSPACE_ROOT"] = str(src)
from swarm_os.services.trust_ledger import grant
for scope in ("filesystem", "sandbox_repl", "mcp", "web_fetch"):
    grant(scope, 8 * 3600)
print("  offline grants: filesystem, sandbox_repl, mcp, web_fetch (8h)")

item = {
    "id": instance_id,
    "prompt": cls._build_prompt(inst, hf_inst),
    "split": "repair",
}
print(f"  prompt length: {len(item['prompt'])} chars")
print(f"  SWARM_WORKSPACE_ROOT: {os.environ.get('SWARM_WORKSPACE_ROOT')}")
print(f"  SWARM_ROUTING_MODE: {os.environ.get('SWARM_ROUTING_MODE', 'not set')}")

import time
t0 = time.time()
res = rc._attempt_once(item, 1200, allow_approval=True, record=False)
elapsed = time.time() - t0

rc._revoke_offline()
os.environ.pop("SWARM_WORKSPACE_ROOT", None)

print()
print(f"  elapsed: {elapsed:.1f}s")
print(f"  cli_ok: {res.get('cli_ok')}")
print(f"  timed_out: {res.get('timed_out')}")
print(f"  tool_order: {res.get('tool_order', [])}")
print(f"  content length: {len(res.get('content', ''))} chars")
print(f"  files_changed: {res.get('files_changed', [])}")

# Show git diff
diff_stat = probe._run(["git", "diff", "--stat", base_commit, "HEAD"], src)
print(f"  git diff --stat: {diff_stat[1].strip()}")
diff_full = probe._run(["git", "diff", base_commit, "HEAD"], src)
print("  git diff (full):")
print(diff_full[1][:2000])

print()
print("=" * 60)
print("STEP 4: Verify post-repair tests")
print("=" * 60)
cls._clean_shadowing_metadata(src)
cmd = _test_cmd(py, test_cmd)
rc_code2, after_output = _run(cmd, src, timeout=120)
f2p_p2, f2p_f2 = _f2p_result(after_output, f2p)
print(f"  pytest exit code: {rc_code2}")
print(f"  F2P: {f2p_p2} passed, {f2p_f2} failed (target: 3 passed, 0 failed)")

ok, reason = cls._test_result(after_output, f2p, [], set())
print(f"  verdict: ok={ok} reason={reason}")
print(f"  after_failing: {sorted(cls._failing_ids(after_output))}")

print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
result = {
    "instance_id": instance_id,
    "base_commit": base_commit,
    "baseline_f2p_failed": f2p_f,
    "baseline_f2p_passed": f2p_p,
    "agent_cli_ok": res.get("cli_ok"),
    "agent_timed_out": res.get("timed_out"),
    "agent_tool_order": res.get("tool_order", []),
    "agent_elapsed_s": round(elapsed, 1),
    "post_f2p_passed": f2p_p2,
    "post_f2p_failed": f2p_f2,
    "verdict": ok,
    "verify_reason": reason,
    "diff_stat": diff_stat[1].strip(),
}
out_file = ROOT / "qwen_train" / "results" / "twine_eval_$(date +%Y%m%d_%H%M%S).json"
out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
