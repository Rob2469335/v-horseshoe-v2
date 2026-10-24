"""Regression tests: the protected runner must VALIDATE before it CONSUMES.

Governing principle (`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md`,
`F2-IMPL-AUTH-033` section 2): consumption happens "immediately before the child
spawn (a readiness failure never burns the grant)". The S8 runner previously
called `verify_and_consume` first, so a malformed manifest, an empty runnable set,
a bad `--only` selection or missing governed env would consume a single-use grant
and *then* refuse.

The proof used here needs NO grant, NO signing key and NO trust root: with no
trust root `verify_and_consume` fails closed with an AUTHORIZATION error, so if a
bad request is instead refused with the VALIDATION message, validation provably
ran first. Nothing here invokes the real runner against a real manifest or grant,
and no VM, guest or network operation occurs.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train import f2_s8_pilot_live as live  # noqa: E402

SOURCE = (REPO / "qwen_train" / "f2_s8_pilot_live.py").read_text(encoding="utf-8")
BODY = SOURCE[SOURCE.index("def main("):]


def _gates(tmp_path):
    """All required gate booleans true -- preconditions, not authority."""
    g = tmp_path / "gates.json"
    g.write_text(json.dumps({name: True for name in live.REQUIRED_GATES}),
                 encoding="utf-8")
    return g


def _run(tmp_path, *, manifest_text, inputs_dir=None, only=None):
    m = tmp_path / "manifest.json"
    m.write_text(manifest_text, encoding="utf-8")
    argv = [
        "--manifest", str(m),
        "--inputs", str(inputs_dir or tmp_path),
        "--registration-id", "test_reg",
        "--registration-sha256", "a" * 64,
        "--ledger", str(tmp_path / "ledger.jsonl"),
        "--staging", str(tmp_path / "stage"),
        "--out", str(tmp_path / "out"),
        "--gates", str(_gates(tmp_path)),
    ]
    if only is not None:
        argv += ["--only", *only]
    try:
        live.main(argv)
    except SystemExit as ei:
        return str(ei)
    except Exception as exc:  # noqa: BLE001 - a readiness refusal, not a SystemExit
        return f"{type(exc).__name__}: {exc}"
    return "<no exit>"


# --------------------------------------------------------------- structural order
class TestValidationPrecedesConsumption:
    """Ordering of the live runner (v2 flow): validation -> readiness -> RESERVE.

    The legacy single-use consumer (``verify_and_consume``) is no longer part of
    the live path at all (v2 grants consume budget via ``verify_and_reserve``;
    v1 is refused). These checks inspect real call sites, not comments.
    """

    @staticmethod
    def _code_only(src: str) -> str:
        import io
        import tokenize

        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return " ".join(out)

    def test_source_orders_validation_before_reservation(self):
        code = self._code_only(BODY)
        reserve = code.index("verify_and_reserve (")
        assert code.index("build_specs (") < reserve, (
            "build_specs must run before the grant budget is reserved")
        assert code.index("load_governance_from_env") < reserve, (
            "readiness (governed env) must run before the grant budget is reserved")
        assert BODY.index("zero runnable tasks") < BODY.index("verify_and_reserve("), (
            "the zero-runnable refusal must run before the budget is reserved")

    def test_legacy_consumer_is_not_called_by_the_live_runner(self):
        code = self._code_only(BODY)
        assert "verify_and_consume" not in code, (
            "the live runner must not call the legacy consumer (a v2 grant would be "
            "refused there and a v1 grant must never run live)")
        assert "verify_and_reserve (" in code, "reservation must still happen"
        # the non-consuming check may only PRECEDE reservation, never replace it
        assert code.index("require_authorized (") < code.index("verify_and_reserve (")
        # --only must not narrow the signed binding: inspect the reserve call
        start = BODY.index("verify_and_reserve(\n")
        call = BODY[start:start + 1500]
        assert "candidate_manifest_digest" in call and "registration_sha256" in call
        assert "candidate_pool_digest" in call
        assert "args.only" not in call, "--only must not narrow the reserved grant scope"


# --------------------------------------------------------- non-consuming refusals
class TestInvalidRequestsDoNotReachConsumption:
    def test_malformed_manifest_refused_at_parse(self, tmp_path):
        """A parse failure is NOT an authorization refusal (so nothing was consumed)."""
        m = tmp_path / "manifest.json"
        m.write_text('{"a": {"status": "built"', encoding="utf-8")  # broken JSON
        argv = [
            "--manifest", str(m), "--inputs", str(tmp_path),
            "--registration-id", "r", "--registration-sha256", "a" * 64,
            "--ledger", str(tmp_path / "l.jsonl"),
            "--staging", str(tmp_path / "s"), "--out", str(tmp_path / "o"),
            "--gates", str(_gates(tmp_path)),
        ]
        with pytest.raises(Exception) as ei:
            live.main(argv)
        assert not isinstance(ei.value, SystemExit), (
            "a parse failure should surface as a parse error, not a runner refusal")
        assert "authorization" not in str(ei.value).lower()

    def test_zero_runnable_manifest_refused_before_consumption(self, tmp_path):
        msg = _run(tmp_path, manifest_text=json.dumps(
            {"t1": {"status": "dryrun_complete"}}))
        assert "zero runnable tasks" in msg, msg
        assert "authorization" not in msg.lower(), (
            f"validation must precede consumption; got {msg!r}")
        assert "No grant was consumed" in msg

    def test_unknown_only_selection_refused_before_consumption(self, tmp_path):
        msg = _run(tmp_path, manifest_text=json.dumps(
            {"t1": {"status": "dryrun_complete"}}), only=["not-a-task"])
        assert "REFUSING" in msg and "authorization" not in msg.lower(), msg

    def test_scheduler_manifest_is_not_silently_a_build_manifest(self, tmp_path):
        """A scheduler/planning object yields zero specs -- never a silent run."""
        msg = _run(tmp_path, manifest_text=json.dumps({
            "status": "planned", "purpose": "phase2", "selection": ["a", "b"],
            "counts_chain1": 3, "notes": "x",
        }))
        assert "zero runnable tasks" in msg, msg
        assert "No grant was consumed" in msg


# ------------------------------------------------ a valid request still needs auth
class TestValidRequestStillRequiresAuthorization:
    def _inputs(self, tmp_path):
        d = tmp_path / "inputs"
        d.mkdir()
        (d / "t1.json").write_text(json.dumps({
            "repo": "example/repo",
            "base_commit": "0" * 40,
            "test_cmds": ["pytest tests/test_x.py"],
            "FAIL_TO_PASS": ["tests/test_x.py::test_a"],
            "PASS_TO_PASS": [],
        }), encoding="utf-8")
        return d

    def test_a_built_entry_without_a_workspace_is_refused_before_consumption(self, tmp_path):
        """A selected entry must be able to supply its workspace.

        `build_specs` accepts any object with status="built", but `per_task_env`
        reads `entry["workspace"]` once per task *after* consumption -- so a built
        entry carrying no workspace consumed the single-use grant and then aborted
        the whole run with a KeyError. Refused here, before consumption.
        """
        msg = _run(tmp_path, manifest_text=json.dumps({"t1": {"status": "built"}}),
                   inputs_dir=self._inputs(tmp_path))
        assert "workspace" in msg, msg
        assert "No grant was consumed" in msg
        assert "authorization" not in msg.lower(), (
            f"validation must precede consumption; got {msg!r}")

    def test_valid_manifest_passes_validation_and_is_refused_later(self, tmp_path):
        """A valid request must NOT be refused by validation, and must still be
        refused by a later gate (readiness or authorization). Nothing here is
        consumed: with no governed env the run is refused BEFORE consumption --
        which is the point of the correction."""
        msg = _run(tmp_path,
                   manifest_text=json.dumps(
                       {"t1": {"status": "built",
                               "workspace": str(tmp_path / "ws")}}),
                   inputs_dir=self._inputs(tmp_path))
        assert "zero runnable tasks" not in msg, (
            f"a valid build manifest must pass validation; got {msg!r}")
        assert "no 'workspace'" not in msg, (
            f"a well-formed built entry must pass validation; got {msg!r}")
        low = msg.lower()
        assert any(k in low for k in (
            "authorization", "s8-001", "governance", "trusted", "env", "swarm_")), (
            f"a valid request must still be refused by a later gate: {msg!r}")
