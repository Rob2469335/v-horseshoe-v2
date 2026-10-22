"""``f2-execution-state/v1`` measurement contract — tests.

Covers: deterministic measurement, digest binding, tampering, wrong task /
execution / environment, base-gold role substitution, malformed records, missing
inputs, stale evidence, replay, a fabricated bare digest, equal-state reporting,
and integration with the ACTUAL consumers
(``f2_s8_compose.load_governance_from_env`` / ``GovernanceInputs``).

No network, no service, no VM, no arm execution, no production store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qwen_train import f2_execution_state as es

VALID_64 = "a" * 64
OTHER_64 = "b" * 64


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _make_tree(root: Path, *, tag: str = "base") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text(f"print({tag!r})\n", encoding="utf-8")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    (root / "sub").mkdir(exist_ok=True)
    (root / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
    return root


def _state(
    tmp_path: Path,
    *,
    side: str = "base",
    tag: str | None = None,
    **over: object,
) -> es.ExecutionState:
    root = _make_tree(tmp_path / f"tree-{side}-{over.get('_uniq', 'x')}", tag=tag or side)
    payload: dict[str, object] = dict(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        execution_state_identity=side,
        environment_identity="s8-f2-vm1",
        test_command="python -m pytest -q",
        root=root,
        evaluator_implementation_digest=VALID_64,
        procedure_id="f2-s8-evaluator/v1",
        protocol_version="1",
        test_patch_digest=OTHER_64,
        run_nonce=f"nonce-{side}-1",
        gold_patch_digest=(VALID_64 if side == "gold" else ""),
        measured_at="2026-10-17T00:00:00Z",
        interpreter="cpython-3.14:/guest/.venv/Scripts/python.exe",
        distributions=(VALID_64, 7),
    )
    payload.update(over)
    payload.pop("_uniq", None)
    return es.build_execution_state_from_disk(**payload)


# --------------------------------------------------------------------------
# layer 1 — measurement
# --------------------------------------------------------------------------
def test_measure_tree_is_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t")
    first = es.measure_tree(root)
    second = es.measure_tree(root)
    assert first.entries_digest == second.entries_digest
    assert first.file_count == 3
    assert first.total_bytes > 0

    (root / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert es.measure_tree(root).entries_digest != first.entries_digest


def test_measure_tree_is_independent_of_absolute_mount_point(tmp_path: Path) -> None:
    a = _make_tree(tmp_path / "one")
    b = _make_tree(tmp_path / "two")
    assert es.measure_tree(a).entries_digest == es.measure_tree(b).entries_digest


def test_measure_tree_records_exclusions_inside_the_digest(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t")
    clean = es.measure_tree(root)
    (root / ".venv").mkdir()
    (root / ".venv" / "python.exe").write_bytes(b"binary")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"pyc")
    after = es.measure_tree(root)
    # Excluded content changes neither the source-tree digest ...
    assert after.entries_digest == clean.entries_digest
    # ... nor the file count; but the exclusion set is DECLARED, not silent.
    assert after.file_count == clean.file_count == 3
    assert clean.excluded == ()
    assert after.excluded == (".venv", "__pycache__")


def test_measure_tree_records_symlinks_without_following_them(tmp_path: Path) -> None:
    root = _make_tree(tmp_path / "t")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(outside / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    m = es.measure_tree(root)
    assert m.symlink_count == 1
    assert m.file_count == 3  # the symlink target's bytes are NOT read as a file
    # Repointing the link changes the measurement (the target TEXT is bound).
    os.remove(link)
    os.symlink(outside / "other.txt", link)
    assert es.measure_tree(root).entries_digest != m.entries_digest


def test_measure_tree_missing_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(es.MeasurementError):
        es.measure_tree(tmp_path / "does-not-exist")


def test_measure_tree_unreadable_file_fails_closed(tmp_path: Path, monkeypatch) -> None:
    root = _make_tree(tmp_path / "t")
    real = es._hash_file_exact

    def boom(path: Path):
        if Path(path).name == "app.py":
            raise OSError("permission denied")
        return real(path)

    monkeypatch.setattr(es, "_hash_file_exact", boom)
    with pytest.raises(es.MeasurementError):
        es.measure_tree(root)


# --------------------------------------------------------------------------
# layer 2 — record binding
# --------------------------------------------------------------------------
def test_valid_record_verifies(tmp_path: Path) -> None:
    rec = _state(tmp_path)
    v = es.verify_execution_state(
        rec,
        expected_instance_id="octocat__hello-1",
        expected_repository="octocat/hello",
        expected_base_commit="c" * 40,
        expected_side="base",
        expected_environment_identity="s8-f2-vm1",
    )
    assert v.ok, v.detail
    assert rec.execution_state_digest == rec.compute_digest()
    assert rec.tree_digest in rec.canonical_bytes().decode("utf-8")


def test_record_survives_json_round_trip(tmp_path: Path) -> None:
    rec = _state(tmp_path)
    payload = json.loads(json.dumps(rec.to_dict()))
    v = es.verify_execution_state(payload, expected_instance_id="octocat__hello-1")
    assert v.ok, v.detail
    assert es.ExecutionState.from_dict(payload).canonical_bytes() == rec.canonical_bytes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("tree_digest", "d" * 64),
        ("tree_file_count", 99),
        ("tree_total_bytes", 12345),
        ("interpreter", "cpython-3.14:/somewhere/else/python.exe"),
        ("installed_distributions_digest", "e" * 64),
        ("installed_distribution_count", 3),
        ("evaluator_implementation_digest", "f" * 64),
        ("procedure_id", "other-procedure"),
        ("protocol_version", "2"),
        ("test_patch_digest", "0" * 64),
        ("run_nonce", "nonce-tampered"),
        ("measured_state_digest", "1" * 64),
        ("environment_identity", "somewhere-else"),
        ("base_commit", "9" * 40),
        ("instance_id", "other__task-1"),
        ("excluded_paths", ["hidden"]),
    ],
)
def test_tampered_measurement_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    rec = _state(tmp_path)
    tampered = es.ExecutionState.from_dict({**rec.to_dict(), field: value})
    v = es.verify_execution_state(tampered)
    assert v.state in (
        es.STATE_ES_DIGEST_MISMATCH,
        es.STATE_ES_ENVIRONMENT_MISMATCH,
        es.STATE_ES_MALFORMED,
        es.STATE_ES_IDENTITY_MISMATCH,
    ), f"{field} produced {v.state}: {v.detail}"
    # Integrity is always the load-bearing check for a mutated measurement.
    if v.state != es.STATE_ES_MALFORMED:
        assert not v.measured_ok


def test_fabricated_bare_digest_is_rejected(tmp_path: Path) -> None:
    """A syntactically valid 64-hex string is NOT proof that a state was measured."""
    fabricated = {
        "schema_version": es.SCHEMA_VERSION,
        "experiment": "J",
        "stage": "S8-001",
        "operation": "base_gold_production",
        "instance_id": "octocat__hello-1",
        "repository": "octocat/hello",
        "base_commit": "c" * 40,
        "execution_state_identity": "base",
        "environment_identity": "s8-f2-vm1",
        "test_command": "python -m pytest -q",
        "measurement_algorithm": es.MEASUREMENT_ALGORITHM,
        "canonicalization": es.CANONICALIZATION,
        "tree_digest": VALID_64,
        "tree_file_count": 0,
        "tree_total_bytes": 0,
        "tree_symlink_count": 0,
        "interpreter": "cpython-3.14:/guest/python.exe",
        "installed_distributions_digest": VALID_64,
        "installed_distribution_count": 0,
        "evaluator_implementation_digest": VALID_64,
        "procedure_id": "f2-s8-evaluator/v1",
        "protocol_version": "1",
        "test_patch_digest": OTHER_64,
        "gold_patch_digest": "",
        "run_nonce": "nonce-base-1",
        "excluded_paths": [],
    }
    # Internally consistent digests, but zero measured content: this is the exact
    # shape an operator-supplied assertion would take.
    rec = es.ExecutionState.from_dict(fabricated)
    rec = es.ExecutionState(
        **{**rec.__dict__, "measured_state_digest": rec.compute_measured_state_digest()}
    )
    rec = es.ExecutionState(
        **{**rec.__dict__, "execution_state_digest": rec.compute_digest()}
    )
    assert rec.verify_digest() and rec.measured_state_digest == rec.compute_measured_state_digest()
    v = es.verify_execution_state(rec)
    assert v.state == es.STATE_ES_INCOMPLETE
    assert not v.ok and not v.measured_ok


def test_missing_and_malformed_records(tmp_path: Path) -> None:
    assert es.verify_execution_state(None).state == es.STATE_ES_MISSING
    assert es.verify_execution_state(42).state == es.STATE_ES_MALFORMED  # type: ignore[arg-type]

    rec = _state(tmp_path)
    blank = es.ExecutionState.from_dict({**rec.to_dict(), "instance_id": ""})
    assert es.verify_execution_state(blank).state == es.STATE_ES_MALFORMED

    bad_side = es.ExecutionState.from_dict({**rec.to_dict(), "execution_state_identity": "silver"})
    assert es.verify_execution_state(bad_side).state == es.STATE_ES_MALFORMED

    bad_hex = es.ExecutionState.from_dict({**rec.to_dict(), "tree_digest": "nothex"})
    assert es.verify_execution_state(bad_hex).state == es.STATE_ES_MALFORMED

    bad_int = {**rec.to_dict(), "tree_file_count": "many"}
    assert es.verify_execution_state(bad_int).state == es.STATE_ES_MALFORMED  # type: ignore[arg-type]


def test_unknown_schema_algorithm_and_canonicalization(tmp_path: Path) -> None:
    rec = _state(tmp_path)
    unknown_schema = es.ExecutionState.from_dict({**rec.to_dict(), "schema_version": "f2-execution-state/v9"})
    assert es.verify_execution_state(unknown_schema).state == es.STATE_ES_UNKNOWN_SCHEMA

    # A digest produced under a different algorithm must NOT be reinterpreted.
    other_algo = es.ExecutionState.from_dict({**rec.to_dict(), "measurement_algorithm": "f2-tree-sha256/v2"})
    assert es.verify_execution_state(other_algo).state == es.STATE_ES_UNKNOWN_ALGORITHM

    other_canon = es.ExecutionState.from_dict({**rec.to_dict(), "canonicalization": "json-pretty/v1"})
    assert es.verify_execution_state(other_canon).state == es.STATE_ES_NONCANONICAL

    # ... and the permissive path is available only by explicit opt-in.
    assert es.verify_execution_state(
        other_algo, required_algorithm=None, require_measurement=False
    ).state == es.STATE_ES_DIGEST_MISMATCH


def test_wrong_task_execution_and_environment(tmp_path: Path) -> None:
    rec = _state(tmp_path)
    assert (
        es.verify_execution_state(rec, expected_instance_id="other__task-1").state
        == es.STATE_ES_IDENTITY_MISMATCH
    )
    assert (
        es.verify_execution_state(rec, expected_repository="other/repo").state
        == es.STATE_ES_IDENTITY_MISMATCH
    )
    assert (
        es.verify_execution_state(rec, expected_base_commit="d" * 40).state
        == es.STATE_ES_IDENTITY_MISMATCH
    )
    assert (
        es.verify_execution_state(rec, expected_environment_identity="s8-f2-vm2").state
        == es.STATE_ES_ENVIRONMENT_MISMATCH
    )


def test_role_substitution_is_rejected(tmp_path: Path) -> None:
    base = _state(tmp_path, side="base")
    # A base record offered where a gold record is required.
    assert (
        es.verify_execution_state(base, expected_side="gold").state
        == es.STATE_ES_ROLE_MISMATCH
    )
    # Role/patch incoherence: base declaring a gold patch, gold declaring none.
    base_with_patch = es.ExecutionState.from_dict(
        {**base.to_dict(), "gold_patch_digest": VALID_64}
    )
    assert (
        es.verify_execution_state(base_with_patch).state == es.STATE_ES_ROLE_MISMATCH
    )
    gold = _state(tmp_path, side="gold")
    gold_no_patch = es.ExecutionState.from_dict({**gold.to_dict(), "gold_patch_digest": ""})
    assert (
        es.verify_execution_state(gold_no_patch).state == es.STATE_ES_ROLE_MISMATCH
    )


def test_replay_and_stale_evidence(tmp_path: Path) -> None:
    rec = _state(tmp_path)
    assert (
        es.verify_execution_state(rec, expected_run_nonce="nonce-base-2").state
        == es.STATE_ES_REPLAY
    )
    # In-window is accepted.
    assert es.verify_execution_state(
        rec, valid_from="2026-10-16T00:00:00Z", valid_until="2026-10-18T00:00:00Z"
    ).ok
    # Out-of-window is stale.
    assert (
        es.verify_execution_state(rec, valid_until="2026-10-16T00:00:00Z").state
        == es.STATE_ES_STALE
    )
    assert (
        es.verify_execution_state(rec, valid_from="2026-10-18T00:00:00Z").state
        == es.STATE_ES_STALE
    )
    # A window with no parseable timestamp cannot be evaluated -> fail closed.
    undated = es.ExecutionState.from_dict({**rec.to_dict(), "measured_at": "t0"})
    assert (
        es.verify_execution_state(undated, valid_until="2026-10-18T00:00:00Z").state
        == es.STATE_ES_STALE
    )


# --------------------------------------------------------------------------
# pair semantics
# --------------------------------------------------------------------------
def _pair(tmp_path: Path) -> tuple[es.ExecutionState, es.ExecutionState]:
    base = _state(tmp_path, side="base")
    gold = _state(tmp_path, side="gold")
    return base, gold


def test_valid_pair_verifies(tmp_path: Path) -> None:
    base, gold = _pair(tmp_path)
    v = es.verify_execution_state_pair(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        base_record=base,
        gold_record=gold,
        environment_identity="s8-f2-vm1",
    )
    assert v.ok, v.detail
    assert base.execution_state_digest != gold.execution_state_digest


def test_pair_rejects_swapped_sides(tmp_path: Path) -> None:
    base, gold = _pair(tmp_path)
    v = es.verify_execution_state_pair(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        base_record=gold,
        gold_record=base,
    )
    assert v.state == es.STATE_ES_ROLE_MISMATCH


def test_pair_rejects_wrong_task_and_missing_side(tmp_path: Path) -> None:
    base, gold = _pair(tmp_path)
    v = es.verify_execution_state_pair(
        instance_id="other__task-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        base_record=base,
        gold_record=gold,
    )
    assert v.state == es.STATE_ES_IDENTITY_MISMATCH
    assert (
        es.verify_execution_state_pair(
            instance_id="octocat__hello-1",
            repository="octocat/hello",
            base_commit="c" * 40,
            base_record=base,
            gold_record=None,
        ).state
        == es.STATE_ES_MISSING
    )


def test_equal_base_and_gold_states_are_reported_truthfully(tmp_path: Path) -> None:
    """The contract measures; it does not force the two states apart.

    Identical measured trees must yield identical digests.  Because the S8 pair
    relationship requires two *distinct* states, the pair verifier reports that
    truthfully (``ES_STATES_EQUAL``) instead of fabricating a difference.
    """
    base_tree = _make_tree(tmp_path / "identical-a")
    gold_tree = _make_tree(tmp_path / "identical-b")
    import shutil

    shutil.rmtree(gold_tree)
    shutil.copytree(base_tree, gold_tree)

    common = dict(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        environment_identity="s8-f2-vm1",
        test_command="python -m pytest -q",
        evaluator_implementation_digest=VALID_64,
        procedure_id="f2-s8-evaluator/v1",
        protocol_version="1",
        test_patch_digest=OTHER_64,
        run_nonce="nonce-1",
        measured_at="2026-10-17T00:00:00Z",
        interpreter="cpython-3.14:/guest/python.exe",
        distributions=(VALID_64, 7),
    )
    base = es.build_execution_state_from_disk(
        base_tree, execution_state_identity="base", gold_patch_digest="", **common
    )
    gold = es.build_execution_state_from_disk(
        gold_tree,
        execution_state_identity="gold",
        gold_patch_digest=VALID_64,
        run_nonce="nonce-2",
        **{k: v for k, v in common.items() if k != "run_nonce"},
    )
    # The two records are individually valid ...
    assert es.verify_execution_state(base, expected_side="base").ok
    assert es.verify_execution_state(gold, expected_side="gold").ok
    # ... yet their measured state digests are identical, which the pair reports.
    assert base.tree_digest == gold.tree_digest
    assert base.measured_state_digest == gold.measured_state_digest
    # The two *records* still differ (they carry different side/patch/nonce), so
    # the identity binding is intact.
    assert base.execution_state_digest != gold.execution_state_digest
    v = es.verify_execution_state_pair(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        base_record=base,
        gold_record=gold,
    )
    assert v.state == es.STATE_ES_STATES_EQUAL
    assert not v.ok


# --------------------------------------------------------------------------
# trust boundary
# --------------------------------------------------------------------------
def test_verification_result_cannot_be_forged() -> None:
    with pytest.raises(TypeError):
        es.ExecutionStateVerification(state=es.STATE_ES_VERIFIED)  # type: ignore[call-arg]


# --------------------------------------------------------------------------
# integration with the ACTUAL consumers
# --------------------------------------------------------------------------
def _governance_env(tmp_path: Path, base_digest: str, gold_digest: str) -> dict[str, str]:
    impl = tmp_path / "evaluator_impl.py"
    impl.write_text("# authorized evaluator implementation\n", encoding="utf-8")
    root = tmp_path / "trusted_artifacts"
    root.mkdir(exist_ok=True)
    return {
        "SWARM_S8_ARTIFACT_ROOT": str(root),
        "SWARM_S8_ARTIFACT_RETENTION_DAYS": "30",
        "SWARM_S8_EVALUATOR_ID": "f2-s8-evaluator",
        "SWARM_S8_EVALUATOR_VERSION": "1",
        "SWARM_S8_EVALUATOR_IMPL": str(impl),
        "SWARM_S8_EVALUATOR_PROCEDURE": "f2-s8-procedure",
        "SWARM_S8_ENVIRONMENT_IDENTITY": "s8-f2-vm1",
        "SWARM_S8_BASE_STATE_DIGEST": base_digest,
        "SWARM_S8_GOLD_STATE_DIGEST": gold_digest,
    }


def test_measured_digests_are_accepted_by_the_real_governance_loader(tmp_path: Path) -> None:
    """The measured digest flows unmodified into the EXISTING consumer.

    ``f2_s8_compose`` is not changed: it still consumes an opaque 64-hex digest.
    What changes is where the value comes from -- a measurement instead of an
    operator assertion.  This test proves the substitution is compatible.
    """
    base, gold = _pair(tmp_path)
    env = _governance_env(
        tmp_path, base.measured_state_digest, gold.measured_state_digest
    )
    from qwen_train import f2_s8_compose as compose

    inputs = compose.load_governance_from_env(env)
    assert inputs.environment_identity == "s8-f2-vm1"
    assert inputs.base_state_digest == base.measured_state_digest
    assert inputs.gold_state_digest == gold.measured_state_digest
    assert inputs.state_digest("base") == base.measured_state_digest
    assert inputs.state_digest("gold") == gold.measured_state_digest
    assert inputs.base_state_digest != inputs.gold_state_digest


def test_consumer_and_contract_agree_on_state_distinctness(tmp_path: Path) -> None:
    """The contract's ``ES_STATES_EQUAL`` mirrors the consumer's own refusal."""
    from qwen_train import f2_s8_compose as compose

    env = _governance_env(tmp_path, VALID_64, VALID_64)
    with pytest.raises(compose.S8ComposeError):
        compose.load_governance_from_env(env)


# --------------------------------------------------------------------------
# operator-facing producer CLI
# --------------------------------------------------------------------------
def test_cli_measure_then_verify(tmp_path: Path, capsys) -> None:
    root = _make_tree(tmp_path / "repo")
    impl = tmp_path / "eval.py"
    impl.write_text("# evaluator\n", encoding="utf-8")
    patch = tmp_path / "test_patch.diff"
    patch.write_text("--- a\n+++ b\n", encoding="utf-8")
    out = tmp_path / "base.json"

    rc = es.main(
        [
            "measure",
            "--root", str(root),
            "--side", "base",
            "--instance-id", "octocat__hello-1",
            "--repository", "octocat/hello",
            "--base-commit", "c" * 40,
            "--environment-identity", "s8-f2-vm1",
            "--test-command", "python -m pytest -q",
            "--evaluator-implementation", str(impl),
            "--procedure-id", "f2-s8-procedure",
            "--protocol-version", "1",
            "--test-patch", str(patch),
            "--run-nonce", "nonce-cli-1",
            "--out", str(out),
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert es.verify_execution_state(payload["record"]).ok
    assert payload["measured_state_digest"] == payload["record"]["measured_state_digest"]

    # The CLI refuses a record that does not describe the declared state.
    rc = es.main(
        [
            "verify",
            "--record", str(out),
            "--side", "gold",
            "--instance-id", "octocat__hello-1",
            "--repository", "octocat/hello",
            "--base-commit", "c" * 40,
            "--environment-identity", "s8-f2-vm1",
        ]
    )
    assert rc == 1
    assert "ES_ROLE_MISMATCH" in capsys.readouterr().out

    rc = es.main(
        [
            "verify",
            "--record", str(out),
            "--side", "base",
            "--instance-id", "octocat__hello-1",
            "--repository", "octocat/hello",
            "--base-commit", "c" * 40,
            "--environment-identity", "s8-f2-vm1",
        ]
    )
    assert rc == 0
    assert "ES_VERIFIED" in capsys.readouterr().out


# --------------------------------------------------------------------------
# integration with the ACTUAL preflight path
# --------------------------------------------------------------------------
def test_measured_pair_clears_the_real_preflight_s8_gate(
    tmp_path: Path, monkeypatch
) -> None:
    """A measured pair satisfies the readiness gate it is destined for.

    ``f2_preflight._s8_env_findings`` is the gate the run actually passes
    through.  Feeding it the measured digests must produce READY findings for all
    three S8-only inputs -- proving the contract's output is shaped for the real
    consumer, not merely for a standalone helper.
    """
    base, gold = _pair(tmp_path)
    env = _governance_env(tmp_path, base.measured_state_digest, gold.measured_state_digest)
    for name, value in env.items():
        if name.startswith("SWARM_S8_"):
            monkeypatch.setenv(name, value)

    from qwen_train.f2_preflight import READY, _s8_env_findings

    findings = _s8_env_findings()
    assert findings, "the S8 gate produced no findings at all"
    for finding in findings:
        assert finding.category == READY, f"{finding.ident}: {finding.status} {finding.detail}"
    idents = {f.ident for f in findings}
    assert {
        "env.s8_environment_identity",
        "env.s8_base_state_digest",
        "env.s8_gold_state_digest",
    } <= idents


def test_preflight_shape_gate_alone_cannot_separate_measurement_from_assertion(
    tmp_path: Path, monkeypatch
) -> None:
    """The honest limit of the readiness gate -- and why the contract exists.

    ``_s8_env_findings`` checks presence and SHA-256 *shape* only; it is a
    readiness gate, not a measurement verifier.  A fabricated but well-formed
    digest therefore clears it.  That is a documented Stage-0 property, recorded
    here so the gate is never mistaken for proof: the binding to a real
    measurement comes from ``verify_execution_state``, which rejects the
    fabricated value below.
    """
    base, gold = _pair(tmp_path)
    # a syntactically valid but FABRICATED gold digest: well-formed 64-hex, not
    # the digest of any measured state
    env = _governance_env(tmp_path, base.measured_state_digest, OTHER_64)
    env["SWARM_S8_GOLD_STATE_DIGEST"] = OTHER_64
    for name, value in env.items():
        if name.startswith("SWARM_S8_"):
            monkeypatch.setenv(name, value)

    from qwen_train.f2_preflight import READY, _s8_env_findings

    # the shape gate is satisfied by the fabricated gold digest
    assert all(f.category == READY for f in _s8_env_findings())

    # ...but the contract refuses it: it is not the digest of a measured gold state
    verdict = es.verify_execution_state_pair(
        instance_id="octocat__hello-1",
        repository="octocat/hello",
        base_commit="c" * 40,
        base_record=base,
        gold_record={**gold.to_dict(), "measured_state_digest": OTHER_64},
        environment_identity="s8-f2-vm1",
    )
    assert not verdict.ok
    assert verdict.state in {"ES_DIGEST_MISMATCH", "ES_NONCANONICAL"}
