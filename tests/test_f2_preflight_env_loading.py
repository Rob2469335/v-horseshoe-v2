"""Tests for the preflight operator-environment loader.

Added 2026-10-05 to close a mechanism defect: the operator handoff documents
the receipt key's provision location as ``.env`` (section 1.2) and documents
preflight as a standalone command ``python -m qwen_train.f2_preflight`` (step
K), but ``f2_readiness`` reads ``os.environ`` only. A key correctly placed in
``.env`` was therefore invisible to the gate meant to check it.

These tests pin the loader's safety properties. They never assert a real secret
value: the fixtures use obviously synthetic markers, and one test asserts that
no such marker can appear in the module's own output.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qwen_train import f2_preflight as pf

SYNTHETIC = "unit-test-synthetic-key-not-a-real-secret"


@pytest.fixture()
def isolate_env(monkeypatch):
    """Remove every SWARM_* variable so each test starts from a clean slate."""
    for key in [k for k in os.environ if k.startswith("SWARM_")]:
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


def _write_env(tmp_path, body: str) -> "pathlib.Path":  # noqa: F821
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


class TestLoadsFromEnvFile:
    def test_absent_variable_is_populated(self, tmp_path, isolate_env):
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY={SYNTHETIC}\n")
        pf._load_operator_env(tmp_path)
        assert os.environ.get("SWARM_RECEIPT_KEY") == SYNTHETIC

    def test_quoted_value_is_unquoted(self, tmp_path, isolate_env):
        _write_env(tmp_path, f'SWARM_RECEIPT_KEY="{SYNTHETIC}"\n')
        pf._load_operator_env(tmp_path)
        assert os.environ.get("SWARM_RECEIPT_KEY") == SYNTHETIC
        assert not os.environ["SWARM_RECEIPT_KEY"].startswith('"')

    def test_single_quoted_value_is_unquoted(self, tmp_path, isolate_env):
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY='{SYNTHETIC}'\n")
        pf._load_operator_env(tmp_path)
        assert os.environ.get("SWARM_RECEIPT_KEY") == SYNTHETIC

    def test_value_may_contain_equals(self, tmp_path, isolate_env):
        _write_env(tmp_path, "SWARM_RECEIPT_KEY=aaa=bbb=ccc\n")
        pf._load_operator_env(tmp_path)
        assert os.environ["SWARM_RECEIPT_KEY"] == "aaa=bbb=ccc"


class TestExplicitEnvironmentAlwaysWins:
    def test_existing_export_is_never_overwritten(self, tmp_path, isolate_env):
        os.environ["SWARM_RECEIPT_KEY"] = "operator-exported-value"
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY={SYNTHETIC}\n")
        pf._load_operator_env(tmp_path)
        assert os.environ["SWARM_RECEIPT_KEY"] == "operator-exported-value"

    def test_only_absent_variables_are_filled(self, tmp_path, isolate_env):
        # A PERMITTED key (one of the seven child controls) exercises the
        # "explicit export wins" property; governed execution inputs are covered
        # separately below.
        os.environ["SWARM_AUTONOMY"] = "1"
        _write_env(tmp_path, "SWARM_AUTONOMY=0\nSWARM_EVOLUTION=0\n")
        pf._load_operator_env(tmp_path)
        assert os.environ["SWARM_AUTONOMY"] == "1"   # explicit export is kept
        assert os.environ["SWARM_EVOLUTION"] == "0"  # absent -> filled (allowed key)

    def test_governed_execution_inputs_are_never_imported_from_dotenv(
        self, tmp_path, isolate_env
    ):
        """Process/session-only (owner decision 2026-10-15).

        Importing these from ``.env`` made the preflight look satisfied even
        though the governed execution process reads ``os.environ`` only.
        """
        _write_env(tmp_path, "\n".join([
            "SWARM_DISTILLER_MODEL=robs4b",
            "SWARM_DISTILLER_WEIGHTS_DIGEST=" + "a" * 64,
            "SWARM_F2_EVALUATOR_ID=e",
            "SWARM_F2_EVALUATOR_IMPL=C:/x.py",
            "SWARM_F2_ARTIFACT_ROOT=C:/store",
            "SWARM_F2_ARTIFACT_RETENTION_DAYS=30",
            "SWARM_F2_EMIT_BUNDLE=1",
            "SWARM_F2_TASK_OUTCOME_REPORT=C:/out.json",
            "SWARM_S8_ENVIRONMENT_IDENTITY=env",
            "SWARM_S8_BASE_STATE_DIGEST=" + "a" * 64,
            "SWARM_S8_GOLD_STATE_DIGEST=" + "b" * 64,
            f"SWARM_RECEIPT_KEY={SYNTHETIC}",
        ]) + "\n")
        pf._load_operator_env(tmp_path)
        for key in (
            "SWARM_DISTILLER_MODEL", "SWARM_DISTILLER_WEIGHTS_DIGEST",
            "SWARM_F2_EVALUATOR_ID", "SWARM_F2_EVALUATOR_IMPL",
            "SWARM_F2_ARTIFACT_ROOT", "SWARM_F2_ARTIFACT_RETENTION_DAYS",
            "SWARM_F2_EMIT_BUNDLE", "SWARM_F2_TASK_OUTCOME_REPORT",
            "SWARM_S8_ENVIRONMENT_IDENTITY", "SWARM_S8_BASE_STATE_DIGEST",
            "SWARM_S8_GOLD_STATE_DIGEST",
        ):
            assert key not in os.environ, key
        # ...while the permitted receipt key IS imported.
        assert os.environ["SWARM_RECEIPT_KEY"] == SYNTHETIC
        # The allow-list is exactly the receipt key plus the seven controls.
        assert pf.DOTENV_ALLOWED_KEYS == frozenset(
            {"SWARM_RECEIPT_KEY", *pf.F2_REQUIRED_CONTROLS})

    def test_a_stale_file_cannot_displace_a_live_export(self, isolate_env):
        """The failure mode this guards: an old .env silently replacing a fresh
        operator decision made in the shell."""
        os.environ["SWARM_RECEIPT_KEY"] = "live"
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            _write_env(pathlib.Path(d), "SWARM_RECEIPT_KEY=stale\n")
            pf._load_operator_env(pathlib.Path(d))
        assert os.environ["SWARM_RECEIPT_KEY"] == "live"


class TestFailsClosedOnAbsence:
    def test_missing_env_file_is_not_an_error(self, tmp_path, isolate_env):
        pf._load_operator_env(tmp_path / "no-such-dir")
        assert "SWARM_RECEIPT_KEY" not in os.environ

    def test_missing_file_leaves_environment_untouched(self, tmp_path, isolate_env):
        os.environ["UNRELATED"] = "kept"
        pf._load_operator_env(tmp_path / "no-such-dir")
        assert os.environ["UNRELATED"] == "kept"

    def test_empty_file_is_not_an_error(self, tmp_path, isolate_env):
        _write_env(tmp_path, "")
        pf._load_operator_env(tmp_path)
        assert "SWARM_RECEIPT_KEY" not in os.environ


class TestParsingRules:
    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "# SWARM_RECEIPT_KEY=commented-out\n",
            "SWARM_RECEIPT_KEY\n",
            "  # SWARM_RECEIPT_KEY=nope\n",
        ],
    )
    def test_skipped_lines(self, tmp_path, isolate_env, line):
        _write_env(tmp_path, line)
        pf._load_operator_env(tmp_path)
        assert "SWARM_RECEIPT_KEY" not in os.environ

    def test_blank_key_is_not_loaded(self, tmp_path, isolate_env):
        _write_env(tmp_path, "  =value-without-a-name\n")
        pf._load_operator_env(tmp_path)
        assert "" not in os.environ

    def test_crlf_file_is_handled(self, tmp_path, isolate_env):
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY={SYNTHETIC}\r\n")
        pf._load_operator_env(tmp_path)
        assert os.environ["SWARM_RECEIPT_KEY"] == SYNTHETIC


class TestRepositoryRootResolution:
    def test_env_is_found_at_repo_root_not_cwd(self, tmp_path, isolate_env, monkeypatch):
        """The handoff documents preflight as runnable directly, so .env must be
        located relative to the repository, not the operator's cwd."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        _write_env(repo_root, f"SWARM_RECEIPT_KEY={SYNTHETIC}\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        pf._load_operator_env(repo_root)
        assert os.environ["SWARM_RECEIPT_KEY"] == SYNTHETIC


class TestValuesNeverEscaped:
    def test_loader_returns_none(self, tmp_path, isolate_env):
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY={SYNTHETIC}\n")
        assert pf._load_operator_env(tmp_path) is None

    def test_loader_writes_nothing(self, tmp_path, isolate_env):
        _write_env(tmp_path, f"SWARM_RECEIPT_KEY={SYNTHETIC}\n")
        before = (tmp_path / ".env").read_bytes()
        pf._load_operator_env(tmp_path)
        assert (tmp_path / ".env").read_bytes() == before

    def test_no_real_key_reaches_json_preflight_output(self, tmp_path, isolate_env):
        """The real provisioned secret must never appear in the gate's own report.

        The value is read here only to prove its ABSENCE from the output; it is
        never printed, compared by value anywhere, or returned by any assertion
        message.
        """
        import re
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        real_key = ""
        env_path = repo_root / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*SWARM_RECEIPT_KEY\s*=\s*(\S+)\s*$", line)
                if m:
                    real_key = m.group(1)
                    break
        if not real_key:
            pytest.skip("no provisioned key in .env; absence asserted instead")

        env = {k: v for k, v in os.environ.items() if not k.startswith("SWARM_")}
        proc = subprocess.run(
            [sys.executable, "-m", "qwen_train.f2_preflight", "--json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
        )
        out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
        err = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")
        assert real_key not in out
        assert real_key not in err
        # And a bare hex-blob scan catches any representation of it. Preflight
        # legitimately emits digests of *evidence*, never of credentials, so an
        # absence of 64-hex runs is the expected invariant.
        assert not re.search(r"[0-9a-f]{64}", out)
        del real_key


class TestCliPreflightDoesNotHonourDotenvGovernedInputs:
    """The ACTUAL CLI path, not ``load_governance_from_env()`` in isolation.

    ``python -m qwen_train.f2_preflight`` runs ``_load_operator_env(root)`` then
    ``run_preflight(root=root)``, with ``root`` the repository root resolved from
    ``__file__``.  These tests point ``__file__`` at a synthetic root that carries
    a ``.env``, so the real CLI composition is what is exercised.
    """

    def _synthetic_root(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        (repo_root / "qwen_train").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            pf, "__file__", str(repo_root / "qwen_train" / "f2_preflight.py"))
        # The ~2.7 GB served-GGUF provenance hash is irrelevant here and would
        # dominate the test; the CLI/env wiring is what is under test.
        monkeypatch.setattr(pf, "_provenance_findings", lambda *a, **k: [])
        return repo_root

    def test_dotenv_only_governed_inputs_do_not_produce_ready_findings(
        self, tmp_path, isolate_env, monkeypatch, capsys
    ):
        repo_root = self._synthetic_root(tmp_path, monkeypatch)
        (repo_root / ".env").write_text("\n".join([
            "SWARM_DISTILLER_MODEL=robs4b",
            "SWARM_F2_EMIT_BUNDLE=1",
            "SWARM_F2_EVALUATOR_ID=f2_pytest_junit_evaluator",
            "SWARM_S8_ENVIRONMENT_IDENTITY=synthetic-env",
            "SWARM_S8_BASE_STATE_DIGEST=" + "a" * 64,
            "SWARM_S8_GOLD_STATE_DIGEST=" + "b" * 64,
            f"SWARM_RECEIPT_KEY={SYNTHETIC}",
        ]) + "\n", encoding="utf-8")

        rc = pf.main(["--json"])
        out = capsys.readouterr().out
        by = {f["id"]: f for f in json.loads(out)["findings"]}

        # Governed execution inputs must NOT be satisfied from `.env`.
        assert by["env.distiller_model"]["category"] == pf.OPERATOR_ACTION
        assert by["env.emit_bundle"]["category"] == pf.OPERATOR_ACTION
        assert by["env.evaluator_id"]["category"] == pf.OPERATOR_ACTION
        assert by["env.s8_environment_identity"]["category"] == pf.OPERATOR_ACTION
        assert by["env.s8_base_state_digest"]["category"] == pf.OPERATOR_ACTION
        assert by["env.s8_gold_state_digest"]["category"] == pf.OPERATOR_ACTION
        # The permitted pair is still observed -- presence only.
        assert by["env.receipt_key"]["category"] == pf.READY
        # No value reaches the report, and the run is still BLOCKED.
        assert SYNTHETIC not in out
        assert rc == 1

    def test_process_provided_governed_values_are_observed(
        self, tmp_path, isolate_env, monkeypatch, capsys
    ):
        repo_root = self._synthetic_root(tmp_path, monkeypatch)
        (repo_root / ".env").write_text("", encoding="utf-8")
        monkeypatch.setenv("SWARM_F2_EMIT_BUNDLE", "1")
        monkeypatch.setenv("SWARM_S8_ENVIRONMENT_IDENTITY", "env-1")
        monkeypatch.setenv("SWARM_S8_BASE_STATE_DIGEST", "a" * 64)
        monkeypatch.setenv("SWARM_S8_GOLD_STATE_DIGEST", "b" * 64)

        pf.main(["--json"])
        by = {f["id"]: f for f in json.loads(capsys.readouterr().out)["findings"]}
        assert by["env.emit_bundle"]["category"] == pf.READY
        assert by["env.s8_environment_identity"]["category"] == pf.READY
        assert by["env.s8_base_state_digest"]["category"] == pf.READY
        assert by["env.s8_gold_state_digest"]["category"] == pf.READY