"""Option-C probe: run a SWE-rebench-V2 instance WITHOUT Docker.

The question this answers: can we get a real, verified SWE task pool locally,
with no container runtime and no pod? The instance's own `install_config` says
plain `pip install -e .` and a plain `pytest`, so the path is:

    clone repo@base_commit -> venv -> pip install -> apply test_patch
        -> run test_cmd  => FAIL_TO_PASS must FAIL   (the bug is real)
        -> apply gold patch -> run test_cmd => FAIL_TO_PASS must PASS (solvable)

If both hold, the instance is a usable task and we never needed Docker.
If the env keeps breaking across repos, that is the evidence that justifies a
CPU pod for containers.

Usage:
  python qwen_train/swe_rebench_probe.py --instance pallets__click-2380
  python qwen_train/swe_rebench_probe.py --list --language python
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _atomic import atomic_write_text  # noqa: E402

ROOT = _HERE.parent
WORK = ROOT / "data" / "swe_probe"  # gitignored (data/)
_DS = "https://datasets-server.huggingface.co/rows?dataset=nebius/SWE-rebench-V2&config=default&split=train"


def _get(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _list_rows(pages: int = 4, per: int = 60) -> list[dict]:
    out: list[dict] = []
    for p in range(pages):
        try:
            out += _get(f"{_DS}&offset={p * per}&length={per}")["rows"]
        except Exception as exc:  # noqa: BLE001
            print(f"  page {p} failed: {exc}")
    return [r["row"] for r in out]


def fetch_instance(instance_id: str, pages: int = 4) -> dict | None:
    for row in _list_rows(pages):
        if row.get("instance_id") == instance_id:
            return row
    return None


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def _parse_list_field(v) -> list[str]:
    """SWE-rebench-V2 stores FAIL_TO_PASS/PASS_TO_PASS as a STRINGIFIED list
    (`"['tests/a.py::x', 'tests/a.py::y']"`), not a real list and not
    space-separated — so `str(v).split()` shreds the ids into garbage tokens
    (e.g. `"['tests/a.py::x',"`), which is why every F2P match read as 0/0.
    """
    if isinstance(v, list):
        return [str(x) for x in v]
    s = str(v or "").strip()
    if not s:
        return []
    for parse in (json.loads, ast.literal_eval):
        try:
            got = parse(s)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(got, (list, tuple)):
            return [str(x) for x in got]
    return s.split()


_PY_IMAGE_RE = re.compile(r"python_base_3(\d+)")


def _interpreter_for(base_image_name: str) -> list[str] | None:
    """Map the dataset's `base_image_name` to a LOCAL interpreter launcher.

    Each instance declares the interpreter it was built for
    (`python_base_310` = Python 3.10). Building its venv with a different minor
    version produces environment failures that LOOK like task failures — that is
    exactly how `pyfakefs`/`cliquet` "failed" on this box (3.14 only). Returns
    None when the image cannot be mapped so the caller can fail CLEARLY instead
    of silently guessing with the host interpreter.
    """
    m = _PY_IMAGE_RE.search(str(base_image_name or "").strip())
    if not m:
        return None
    # `python_base_3` + "10" -> "-3.10"; + "9" -> "-3.9"
    return ["py", f"-3.{m.group(1)}"]


def _ensure_interpreter(launcher: list[str], base_image_name: str) -> str | None:
    """Return the resolved version string, or None with a clear diagnosis."""
    rc, out = _run([*launcher, "--version"], Path.cwd(), timeout=60)
    if rc == 0 and out.strip():
        return out.strip().splitlines()[0]
    print(f"  base_image_name={base_image_name!r} -> {' '.join(launcher)} is NOT installed")
    print("  install that interpreter, or skip this instance.")
    print(f"  REFUSING to fall back to {sys.version.split()[0]} — a wrong-interpreter venv")
    print("  produces environment failures that masquerade as task failures.")
    return None


def _pip_cmd(py: Path, step: str) -> list[str] | None:
    """Turn an install_config step (`"pip install -q pytest-socket"`) into argv
    against the INSTANCE venv. Returns None for a non-pip step (caller shells it).

    This matters: the steps carry the repo's TEST dependencies (pytest-socket,
    requirements.txt, …). Running only `pip install -e .` gave a venv where
    `pytest` itself errored on the repo's own pytest.ini addopts — an env
    failure that looked like a task failure.
    """
    s = step.strip()
    for prefix in ("pip3 ", "pip ", "python3 -m pip ", "python -m pip "):
        if s.startswith(prefix):
            return [str(py), "-m", "pip", *s[len(prefix) :].split()]
    return None


def _test_cmd(py: Path, test_cmd: str) -> list[str]:
    """`pytest <args>` -> `<venv python> -m pytest <args>`."""
    parts = test_cmd.split()
    if parts and parts[0] == "pytest":
        return [str(py), "-m", "pytest", *parts[1:]]
    return [str(py), "-m", "pytest"]


def _f2p_result(output: str, f2p: list[str]) -> tuple[int, int]:
    """Count FAIL_TO_PASS tests reported PASSED vs FAILED in pytest -rA output.

    -rA prints one short-summary line per test: `PASSED path::name` /
    `FAILED path::name`, which is exactly the id we were given.
    """
    passed = failed = 0
    for t in f2p:
        if f"PASSED {t}" in output:
            passed += 1
        elif f"FAILED {t}" in output or f"ERROR {t}" in output:
            failed += 1
    return passed, failed


def probe(instance_id: str, pages: int = 4) -> int:
    inst = fetch_instance(instance_id, pages)
    if not inst:
        print(f"instance not found in first {pages * 60} rows: {instance_id}")
        return 2

    repo = inst["repo"]
    base = inst["base_commit"]
    cfg = inst.get("install_config") or {}
    test_cmd = str(cfg.get("test_cmd") or "")
    install = cfg.get("install") or []
    if isinstance(install, str):
        install = [install]
    f2p = _parse_list_field(inst.get("FAIL_TO_PASS"))

    d = WORK / instance_id
    src = d / "repo"
    venv = d / "venv"
    d.mkdir(parents=True, exist_ok=True)

    base_image = str(cfg.get("base_image_name") or "")
    py_launcher = _interpreter_for(base_image)
    if py_launcher is None:
        print(f"instance   : {instance_id}")
        print(f"  base_image_name={base_image!r} is not mappable to a local interpreter")
        print("  refusing to guess — fix the mapping or skip this instance")
        return 5
    py_version = _ensure_interpreter(py_launcher, base_image)
    if py_version is None:
        return 5

    print(f"instance   : {instance_id}")
    print(f"repo       : {repo}@{base[:10]}")
    print(f"interpreter: {py_version}   (declared by base_image_name={base_image})")
    print(f"install    : {install}")
    print(f"test_cmd   : {test_cmd}")
    print(f"FAIL_TO_PASS ({len(f2p)}): {f2p}")

    # 1. clone @ base_commit
    if not (src / ".git").exists():
        print("\n[1/5] cloning …")
        rc, out = _run(["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(src)], ROOT)
        if rc != 0:
            print(f"  clone failed rc={rc}: {out[-300:]}")
            return 3
    rc, out = _run(["git", "checkout", "--quiet", base], src)
    if rc != 0:
        print(f"  checkout failed rc={rc}: {out[-300:]}")
        return 3
    print("[1/5] at base_commit ✓")

    # 2. venv + install
    py = venv / "Scripts" / "python.exe"
    if not py.exists():
        print(f"[2/5] creating venv with {' '.join(py_launcher)} …")
        # The venv MUST be built by the interpreter the instance declares —
        # never the host's. See _interpreter_for.
        rc, out = _run([*py_launcher, "-m", "venv", str(venv)], d, timeout=300)
        if rc != 0:
            print(f"  venv failed: {out[-300:]}")
            return 4
    print("[2/5] installing …")
    _run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], d, timeout=600)
    failed: list[str] = []
    for step in install or ["pip install -q -e ."]:
        argv = _pip_cmd(py, str(step))
        rc, out = (
            _run(argv, src, timeout=1500)
            if argv is not None
            else _run(["cmd", "/c", str(step)], src, timeout=1500)
        )
        if rc != 0:
            failed.append(f"{step} (rc={rc}: {out[-160:]})")
    # A failed STEP is logged, not fatal: some steps are optional and the test
    # run below is the real arbiter of whether the env is usable.
    _run([str(py), "-m", "pip", "install", "-q", "pytest"], src, timeout=600)
    print(f"[2/5] installed ✓ ({len(failed)} step(s) failed)" if failed else "[2/5] installed ✓")
    for f in failed:
        print(f"      ! {f}")

    # 3. apply test_patch (the tests that encode the bug)
    tp = d / "test_patch.diff"
    atomic_write_text(tp, inst.get("test_patch") or "")
    # `git apply --3way` STAGES what it applies, so `git checkout -- .` (which
    # restores from the INDEX) leaves the change in place — a resumed run then
    # measured the gold-patched tree as if it were "base". Reset BOTH.
    _run(["git", "reset", "--hard", "HEAD"], src)
    _run(["git", "clean", "-fd"], src)
    rc, out = _run(["git", "apply", "-v", "--3way", "--recount", "--ignore-space-change", str(tp)], src)
    if rc != 0:
        rc, out = _run(["git", "apply", "-v", str(tp)], src)
    print(f"[3/5] test_patch applied: rc={rc}")

    # 4. run tests at base -> F2P must FAIL
    print("[4/5] running tests at base (expect FAIL_TO_PASS to FAIL) …")
    rc, out = _run(_test_cmd(py, test_cmd), src, timeout=900)
    passed, failed = _f2p_result(out, f2p)
    at_base_ok = failed > 0 and passed == 0
    print(f"      FAIL_TO_PASS passed={passed} failed={failed} -> {'FAILS at base OK' if at_base_ok else 'UNEXPECTED'}")
    (d / "run_at_base.txt").write_text(out, encoding="utf-8")

    # 5. apply gold patch -> F2P must PASS
    print("[5/5] applying gold patch (expect FAIL_TO_PASS to PASS) …")
    gp = d / "gold_patch.diff"
    atomic_write_text(gp, inst.get("patch") or "")
    rc2, out2 = _run(["git", "apply", "-v", "--3way", "--recount", "--ignore-space-change", str(gp)], src)
    if rc2 != 0:
        rc2, out2 = _run(["git", "apply", "-v", str(gp)], src)
    rc3, out3 = _run(_test_cmd(py, test_cmd), src, timeout=900)
    passed2, failed2 = _f2p_result(out3, f2p)
    gold_ok = passed2 > 0 and failed2 == 0
    print(f"      gold patch applied rc={rc2}; FAIL_TO_PASS passed={passed2} failed={failed2} -> {'PASSES OK' if gold_ok else 'UNEXPECTED'}")
    (d / "run_at_gold.txt").write_text(out3, encoding="utf-8")

    verdict = "USABLE (docker-free)" if (at_base_ok and gold_ok) else "NEEDS REVIEW"
    print(f"\nVERDICT: {verdict}")
    print(f"artifacts: {d}")
    return 0 if (at_base_ok and gold_ok) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="SWE-rebench-V2 option-C probe (no Docker)")
    ap.add_argument("--instance", default="pallets__click-2380")
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--language", default="python")
    args = ap.parse_args()

    if args.list:
        rows = _list_rows(args.pages)
        py = [r for r in rows if r.get("language") == args.language]
        print(f"{len(rows)} rows fetched, {len(py)} {args.language}")
        for r in sorted(py, key=lambda x: (x.get("meta") or {}).get("num_modified_lines", 999))[:15]:
            m = r.get("meta") or {}
            print(
                f"  {r['instance_id']:42} lines={m.get('num_modified_lines'):>3} "
                f"cmd={(r.get('install_config') or {}).get('test_cmd', '')[:40]}"
            )
        return 0

    return probe(args.instance, args.pages)


if __name__ == "__main__":
    sys.exit(main())
