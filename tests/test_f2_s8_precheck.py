"""Stage-1 collection-only pre-check regression tests (Phase 1F).

Every test calls the real ``run_precheck`` with an injected collection runner, so
the selection, binding, classification and isolation logic are exercised without
executing a test body or touching the guest.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_factory import AppendOnlyLedger  # noqa: E402
from qwen_train.f2_s8_precheck import (  # noqa: E402
    PrecheckError,
    _pytest_args,
    run_precheck,
)
from tests.conftest import _REAL_POPEN  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_real_git(monkeypatch):
    """The suite mocks subprocess.Popen globally; the pre-check must run the
    REAL `git rev-parse HEAD` (never a commit timestamp).  Restore the genuine
    class for this module only; the injected collection runner still prevents
    any test body from executing."""
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)

TASK = "acme__repo-1"
NODE = "tests/test_mod.py::test_target"
PATCH = (
    "diff --git a/pkg/newmod.py b/pkg/newmod.py\n"
    "--- /dev/null\n"
    "+++ b/pkg/newmod.py\n"
    "@@ -0,0 +1 @@\n"
    "+VALUE = 1\n"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _proc(code: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def _workspace(root: pathlib.Path, task: str) -> pathlib.Path:
    ws = root / "ws" / task
    for tree, venv in (("repo", ".venv"), ("repo_gold", ".venv")):
        py = ws / tree / venv / "Scripts" / "python.exe"
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")
    return ws


def _input(root: pathlib.Path, task: str, *, patch: str = PATCH) -> pathlib.Path:
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / f"{task}.json").write_text(
        json.dumps({
            "instance_id": task,
            "FAIL_TO_PASS": [NODE],
            "PASS_TO_PASS": [],
            "test_cmds": ["pytest -rA"],
            "patch": patch,
        }),
        encoding="utf-8",
    )
    return inputs


def _manifest(root: pathlib.Path, tasks: dict[str, pathlib.Path]) -> pathlib.Path:
    path = root / "build_manifest.json"
    path.write_text(
        json.dumps({t: {"status": "built", "workspace": str(ws)} for t, ws in tasks.items()}),
        encoding="utf-8",
    )
    return path


def _registration(root: pathlib.Path, tasks: list[str]) -> pathlib.Path:
    path = root / "registered_tasks.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def _ledger(root: pathlib.Path, rows: list[dict] | None = None) -> pathlib.Path:
    path = root / "state" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    led = AppendOnlyLedger(path)
    for row in rows or []:
        led.append(row)
    return path


def _ok_runner(*_args, **_kwargs):
    return _proc(0, NODE + "\n")


def _base_factory(root: pathlib.Path, *, tasks: list[str] | None = None):
    tasks = tasks or [TASK]
    workspaces = {t: _workspace(root, t) for t in tasks}
    inputs = _input(root, TASK)
    for t in tasks[1:]:
        _input(root, t)
    return {
        "manifest": _manifest(root, workspaces),
        "registration": _registration(root, tasks),
        "ledger": _ledger(root),
        "inputs": inputs,
        "out": root / "out",
    }


def _run(fx, *, runner=_ok_runner, **over):
    kwargs = dict(
        repo_root=REPO,
        build_manifest_path=fx["manifest"],
        registration_path=fx["registration"],
        registration_id="reg-1",
        registration_sha256="a" * 64,
        ledger_paths=[fx["ledger"]],
        out_dir=fx["out"],
        inputs_dir=fx["inputs"],
        runner=runner,
    )
    kwargs.update(over)
    return run_precheck(**kwargs)


# ---------------------------------------------------------------------------
# valid candidate and determinism
# ---------------------------------------------------------------------------
class TestValidPrecheck:
    def test_a_valid_candidate_passes(self, tmp_path):
        fx = _base_factory(tmp_path)
        result = _run(fx)
        assert result["verdict"] == "PASS", result
        assert result["counts"]["passed"] == 1
        assert result["tasks"][0]["status"] == "PASS"
        assert result["bindings"]["revision"] == subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
        assert len(result["bindings"]["manifest_digest"]) == 64
        assert len(result["bindings"]["run_id"]) == 64

    def test_repeated_prechecks_are_deterministic(self, tmp_path):
        fx = _base_factory(tmp_path)
        first = _run(fx)
        second = _run(fx)
        assert first["result_digest"] == second["result_digest"]
        assert first["bindings"]["run_id"] == second["bindings"]["run_id"]

    def test_the_result_claims_no_credit(self, tmp_path):
        fx = _base_factory(tmp_path)
        result = _run(fx)
        blob = json.dumps(result)
        assert '"admitted"' not in blob
        assert '"credited"' not in blob
        assert "collection-only compatibility is NOT scientific admission" in result["notes"]


# ---------------------------------------------------------------------------
# selection and exclusion
# ---------------------------------------------------------------------------
class TestSelection:
    def test_an_unregistered_request_is_excluded(self, tmp_path):
        fx = _base_factory(tmp_path)
        result = _run(fx, tasks=["nope"])
        assert result["verdict"] == "INCONCLUSIVE"
        assert {"task_id": "nope", "reason": "UNREGISTERED"} in result["excluded"]

    def test_a_previously_recorded_task_is_excluded(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK, "acme__repo-2"])
        fx["ledger"] = _ledger(
            tmp_path,
            [{"task_id": "acme__repo-2", "outcome": "gold_fail",
              "outcome_class": "scientific", "admitted": False}],
        )
        result = _run(fx)
        assert "acme__repo-2" not in [t["task_id"] for t in result["tasks"]]
        assert {"task_id": "acme__repo-2", "reason": "PREVIOUSLY_RECORDED"} in result["excluded"]

    def test_the_historical_exclusion_is_enforced(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK, "networkx__networkx-8207"])
        result = _run(fx)
        assert {"task_id": "networkx__networkx-8207", "reason": "EXCLUDED_BY_POLICY"} in result["excluded"]

    def test_the_parked_task_is_not_a_candidate(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK, "stanfordnlp__dspy-8739"])
        result = _run(fx)
        assert {"task_id": "stanfordnlp__dspy-8739", "reason": "PARKED_BY_POLICY"} in result["excluded"]

    def test_a_task_at_its_retry_cap_is_excluded(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK, "acme__repo-3"])
        fx["ledger"] = _ledger(
            tmp_path,
            [
                {"task_id": "acme__repo-3", "outcome": "infrastructure_failure",
                 "outcome_class": "infrastructure", "admitted": False},
            ] * 3,
        )
        result = _run(fx)
        assert {"task_id": "acme__repo-3", "reason": "RETRY_CAP_EXHAUSTED"} in result["excluded"]

    def test_only_built_tasks_are_candidates(self, tmp_path):
        fx = _base_factory(tmp_path, tasks=[TASK])
        # Add a registered task with no built workspace.
        fx["registration"].write_text(json.dumps([TASK, "acme__unbuilt"]), encoding="utf-8")
        result = _run(fx)
        assert [t["task_id"] for t in result["tasks"]] == [TASK]
        assert result["counts"]["registered_unbuilt"] == 1


# ---------------------------------------------------------------------------
# stale bindings
# ---------------------------------------------------------------------------
class TestBindings:
    def test_a_stale_revision_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, expected_revision="0" * 40)

    def test_a_stale_manifest_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, expected_manifest_digest="0" * 64)

    def test_a_stale_registration_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, expected_registration_sha256="0" * 64)

    def test_a_changed_task_list_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, expected_task_list_digest="0" * 64)

    def test_a_stale_policy_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, expected_policy_digest="0" * 64)


# ---------------------------------------------------------------------------
# collection outcomes
# ---------------------------------------------------------------------------
class TestCollectionOutcomes:
    def test_a_project_owned_base_collection_error_is_expected(self, tmp_path):
        fx = _base_factory(tmp_path)

        def runner(cmd, cwd, env, timeout):
            if cwd.name == "repo":
                return _proc(2, "", "ERROR tests/test_mod.py - ModuleNotFoundError: No module named 'pkg.newmod'")
            return _proc(0, NODE + "\n")

        result = _run(fx, runner=runner)
        assert result["verdict"] == "PASS", result
        assert result["tasks"][0]["sides"]["base"]["note"] == "PROJECT_COLLECTION_EXPECTED_ON_BASE"

    def test_an_environment_collection_error_fails(self, tmp_path):
        fx = _base_factory(tmp_path)

        def runner(cmd, cwd, env, timeout):
            if cwd.name == "repo":
                return _proc(2, "", "ERROR tests/test_mod.py - ModuleNotFoundError: No module named 'fastapi'")
            return _proc(0, NODE + "\n")

        result = _run(fx, runner=runner)
        assert result["verdict"] == "FAIL"
        assert "ENVIRONMENT_COLLECTION_ERROR" in result["tasks"][0]["reason_codes"]
        assert result["tasks"][0]["parked"] is True

    def test_a_gold_project_collection_error_fails(self, tmp_path):
        fx = _base_factory(tmp_path)

        def runner(cmd, cwd, env, timeout):
            if cwd.name == "repo":
                return _proc(2, "", "ERROR tests/test_mod.py - ModuleNotFoundError: No module named 'pkg.newmod'")
            return _proc(2, "", "ERROR tests/test_mod.py - ModuleNotFoundError: No module named 'pkg.newmod'")

        result = _run(fx, runner=runner)
        assert result["verdict"] == "FAIL"
        assert "GOLD_PROJECT_COLLECTION_ERROR" in result["tasks"][0]["reason_codes"]

    def test_a_missing_target_is_inconclusive(self, tmp_path):
        fx = _base_factory(tmp_path)
        result = _run(fx, runner=lambda *a, **k: _proc(0, "tests/other.py::test_x\n"))
        assert result["verdict"] == "INCONCLUSIVE"
        assert "TARGET_NOT_COLLECTED" in result["tasks"][0]["reason_codes"]

    def test_no_tests_fails(self, tmp_path):
        fx = _base_factory(tmp_path)
        result = _run(fx, runner=lambda *a, **k: _proc(5, "", "no tests ran\n"))
        assert result["verdict"] == "FAIL"
        assert "NO_TESTS_COLLECTED" in result["tasks"][0]["reason_codes"]

    def test_a_timeout_is_inconclusive(self, tmp_path):
        fx = _base_factory(tmp_path)

        def runner(cmd, cwd, env, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        result = _run(fx, runner=runner)
        assert result["verdict"] == "INCONCLUSIVE"
        assert "COLLECT_TIMEOUT" in result["tasks"][0]["reason_codes"]

    def test_a_missing_workspace_is_parked(self, tmp_path):
        fx = _base_factory(tmp_path)
        fx["manifest"].write_text(json.dumps({TASK: {"status": "built", "workspace": str(tmp_path / "absent")}}), encoding="utf-8")
        result = _run(fx)
        assert result["verdict"] == "INCONCLUSIVE"
        assert "WORKSPACE_MISSING" in result["tasks"][0]["reason_codes"]


# ---------------------------------------------------------------------------
# isolation and read-only guarantees
# ---------------------------------------------------------------------------
class TestIsolation:
    def test_an_output_dir_inside_the_ledger_state_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, out_dir=fx["ledger"].parent)

    def test_a_pilot_results_output_dir_is_refused(self, tmp_path):
        fx = _base_factory(tmp_path)
        with pytest.raises(PrecheckError):
            _run(fx, out_dir=tmp_path / "pilot_results_leak")

    def test_the_ledger_and_trust_root_are_unchanged(self, tmp_path):
        fx = _base_factory(tmp_path)
        authz = pathlib.Path(r"C:\Users\rober\F2_AUTHZ")
        watched = [fx["ledger"]]
        watched += sorted(authz.glob("*.json"))
        watched += sorted((authz / "ledger").glob("*.json"))
        before = {str(p): _sha(p.read_bytes()) for p in watched if p.is_file()}
        _run(fx)
        after = {str(p): _sha(p.read_bytes()) for p in watched if p.is_file()}
        assert before == after


class TestPytestArgs:
    def test_uv_run_is_stripped(self):
        assert _pytest_args("uv run pytest -rA") == []

    def test_a_path_and_pyargs_are_kept(self):
        assert _pytest_args("pytest -rA --pyargs networkx") == ["--pyargs", "networkx"]

    def test_a_shell_operator_is_refused(self):
        with pytest.raises(PrecheckError):
            _pytest_args("pytest -q tests/ && rm -rf /")

    def test_a_non_pytest_command_is_refused(self):
        with pytest.raises(PrecheckError):
            _pytest_args("python script.py")

class TestRunnerPrefixes:
    """Measured defect: ``poetry run pytest`` was refused by the legacy prefix
    parser while the shared analyser accepted it, so a collectable task
    reported TEST_CMD_UNSUPPORTED."""

    def test_poetry_run_pytest_is_parsed(self):
        from qwen_train.f2_s8_precheck import _pytest_args
        assert _pytest_args(
            "poetry run pytest -vv -n 4 -rA tests/test_litellm") == [
            "-n", "4", "tests/test_litellm"]

    def test_pdm_run_pytest_is_parsed(self):
        from qwen_train.f2_s8_precheck import _pytest_args
        assert _pytest_args("pdm run pytest -rA tests") == ["tests"]

    def test_uv_run_still_parses(self):
        from qwen_train.f2_s8_precheck import _pytest_args
        assert _pytest_args("uv run pytest -rA") == []

    def test_a_non_pytest_command_is_still_refused(self):
        from qwen_train.f2_s8_precheck import PrecheckError, _pytest_args
        import pytest as _pytest
        with _pytest.raises(PrecheckError):
            _pytest_args("make test")


class TestDeclaredAdjustmentExtras:
    def test_dvc_instances_curate_their_declared_remote_extras(self):
        from qwen_train.f2_s8_acquisition import DECLARED_ADJUSTMENTS
        for task in ("iterative__dvc-10842", "iterative__dvc-10843",
                     "iterative__dvc-10844"):
            assert DECLARED_ADJUSTMENTS[task]["extras"] == [
                "s3", "oss", "gs", "webdav", "webhdfs", "hdfs"]

    def test_the_curated_dvc_extras_are_all_declared_by_the_project(self):
        """Every curated extra must be one the project ITSELF declares -- this is
        per-task curation of a declared source, never an invented package."""
        from qwen_train.f2_s8_acquisition import DECLARED_ADJUSTMENTS
        declared = {"s3", "oss", "gs", "webdav", "webhdfs", "hdfs",
                    "azure", "gdrive", "ssh", "ssh_gssapi", "webhdfs_kerberos",
                    "all", "dev", "lint", "testing", "tests"}
        curated = DECLARED_ADJUSTMENTS["iterative__dvc-10842"]["extras"]
        assert set(curated) <= declared

class TestBatchCapRaised:
    """Owner-authorised raise 10 -> 50 (2026-10-19). The ceiling guard must
    track the approved cap and still refuse anything above it."""

    def test_default_policy_cap_is_50(self):
        from qwen_train.f2_s8_precheck import DEFAULT_POLICY
        assert DEFAULT_POLICY["max_tasks_per_batch"] == 50

    def test_phase_ops_max_batch_is_50(self):
        from qwen_train.f2_s8_phase_ops import MAX_BATCH
        assert MAX_BATCH == 50

    def test_a_policy_above_the_approved_cap_is_still_refused(self):
        from qwen_train.f2_s8_precheck import PrecheckError, _policy
        import pytest as _pytest
        with _pytest.raises(PrecheckError, match="50"):
            _policy({"max_tasks_per_batch": 51})

    def test_the_approved_cap_itself_is_accepted(self):
        from qwen_train.f2_s8_precheck import _policy
        assert _policy({"max_tasks_per_batch": 50})["max_tasks_per_batch"] == 50

class TestEvidenceAmbiguousParking:
    """Owner remedy 1 (2026-10-19): a permanently evidence-ambiguous task is
    parked BEFORE manifest construction, so it cannot invalidate the batch audit
    of its clean siblings -- the r4 failure mode."""

    def test_quant_econ_is_parked(self):
        from qwen_train.f2_s8_precheck import DEFAULT_POLICY
        assert "QuantEcon__QuantEcon.py-797" in DEFAULT_POLICY["parked_tasks"]

    def test_the_original_parked_task_is_preserved(self):
        from qwen_train.f2_s8_precheck import DEFAULT_POLICY
        assert "stanfordnlp__dspy-8739" in DEFAULT_POLICY["parked_tasks"]

    def test_a_parked_task_is_excluded_with_an_explicit_reason(self):
        from qwen_train.f2_s8_precheck import (
            REASON_PARKED, DEFAULT_POLICY, select_candidates,
        )
        built = {"QuantEcon__QuantEcon.py-797": {"status": "built"},
                 "acme__repo-1": {"status": "built"}}
        selected, excluded = select_candidates(
            registered=["QuantEcon__QuantEcon.py-797", "acme__repo-1"],
            build_manifest=built,
            attempts={}, terminal=set(), recorded=set(), policy=DEFAULT_POLICY,
        )
        assert "QuantEcon__QuantEcon.py-797" not in selected
        assert {"task_id": "QuantEcon__QuantEcon.py-797",
                "reason": REASON_PARKED} in excluded
