"""Scan-test contract for process_failure(...) call sites.

Ruling (2026-09-20): task_id= is required only at the six runtime_v2 agent-loop
exit sites (five in agent_service_v2.py + stream_runner.py). watch_loop.py
deliberately passes source="watch-loop" with NO task_id (it mints its own random
run id and is excluded from evidence counting). control.py, reflection_loop.py,
repair_engine.py, healing_watchman.py and organism_console/loops/autonomous.py
are system callers — file-set-allowed and exempt from task_id=/source=.

The file-set guard: the set of files containing process_failure( calls must
equal the expected set, so a new caller in any other file fails the test and
forces a conscious decision (the silent-loss class during the 2026-09 reset).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_RUNTIME_FILES = {
    "runtime_v2/api/agent_service_v2.py",
    "runtime_v2/services/stream_runner.py",
}
_EXPECTED_LABELS = {
    "primary",
    "loop-cycle",
    "forced-synthesis",
    "circuit-breaker",
    "turn-budget",
    "stream_runner",
}
_SYSTEM_FILES = {
    "swarm_os/services/watch_loop.py",
    "swarm_os/api/control.py",
    "swarm_os/services/reflection_loop.py",
    "organism_console/core/repair_engine.py",
    "organism_console/core/healing_watchman.py",
    "organism_console/loops/autonomous.py",
}
_EXPECTED_FILE_SET = _RUNTIME_FILES | _SYSTEM_FILES

_EXCLUDED_PARTS = ("tests", ".venv", ".env.bak", "backup", "__pycache__")


def _iter_py_files():
    for p in ROOT.rglob("*.py"):
        parts = p.parts
        if any(ex in parts for ex in _EXCLUDED_PARTS):
            continue
        yield p


def _process_failure_calls(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "process_failure":
            out.append(node)
    return out


def _rel(p):
    return str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")


def test_runtime_exit_sites_carry_task_id_and_source():
    for rel in _RUNTIME_FILES:
        calls = _process_failure_calls(ROOT / rel)
        assert calls, f"{rel}: no process_failure call found"
        for call in calls:
            kwargs = {k.arg for k in call.keywords if k.arg}
            assert "task_id" in kwargs, f"{rel}:{call.lineno} missing task_id="
            assert "source" in kwargs, f"{rel}:{call.lineno} missing source="


def test_runtime_label_set_is_exact():
    labels = set()
    for rel in _RUNTIME_FILES:
        for call in _process_failure_calls(ROOT / rel):
            for k in call.keywords:
                if k.arg == "source" and isinstance(k.value, ast.Constant):
                    labels.add(k.value.value)
    assert labels == _EXPECTED_LABELS, f"label set mismatch: {labels}"


def test_watch_loop_passes_watch_loop_source_and_no_task_id():
    for call in _process_failure_calls(ROOT / "swarm_os/services/watch_loop.py"):
        kwargs = {k.arg: k.value for k in call.keywords if k.arg}
        assert "task_id" not in kwargs, f"watch_loop must NOT pass task_id= (line {call.lineno})"
        src = kwargs.get("source")
        assert (
            isinstance(src, ast.Constant) and src.value == "watch-loop"
        ), f"watch_loop source must be 'watch-loop' (line {call.lineno})"


def test_file_set_of_process_failure_callers_is_exact():
    callers = {_rel(p) for p in _iter_py_files() if _process_failure_calls(p)}
    assert callers == _EXPECTED_FILE_SET, (
        f"\nunexpected callers: {sorted(callers - _EXPECTED_FILE_SET)}\n"
        f"missing callers:    {sorted(_EXPECTED_FILE_SET - callers)}"
    )