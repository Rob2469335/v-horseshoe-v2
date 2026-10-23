"""Real-``main()`` tests for the v2 gate wiring in the live S8 runner.

Every fixture is synthetic and temporary: ephemeral ed25519 key, temporary
trust root, temporary consumption ledger, temporary budget root, temporary
registration/pool/manifest. No production grant, key, ledger, budget root or
task is touched, and no build, precheck, staging or VM operation runs.

The integration contract under test:

    authoritative grant verification -> v2_preflight -> verify_and_consume once
        -> task runner

and, for every refusal, that NOTHING of that happens: no consumption row, no
task call, no run directory, no budget claim.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
from types import SimpleNamespace

import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_authz_budget as BUDGET
from qwen_train import f2_s8_pilot_live as RUNNER
from qwen_train.f2_authz_ledger import AuthorizationLedger
from qwen_train.f2_s8_candidate_pool import build_pool, canonical_bytes, pool_digest
from qwen_train.f2_s8_factory import ADMITTED_OUTCOME, AppendOnlyLedger
from qwen_train.f2_s8_pilot import PilotTaskResult
from tests.f2_authz_testkit import (
    apply_repo_identity,
    keypair,
    now,
    signed_record,
    use_test_ledger,
    write_record,
    write_trust_root,
)

CONTENT = "c" * 64
TASK = "acme__widget-1"
OTHER = "acme__gadget-2"
REG_BYTES = json.dumps([TASK, OTHER]).encode("utf-8")

GATES = {
    "authority_current": True,
    "d4_budget_approved": True,
    "d4_storage_cap_approved": True,
    "approved_subnet": True,
    "d5_credential_available": True,
    "evaluator_identity_named": True,
    "isolation_verified": True,
    "pilot_authorized": True,
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class FakeChannel:
    """No-op guest channel: proves no real transport is opened."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, str]] = []

    def put(self, local, remote: str) -> None:
        self.puts.append((str(local), remote))

    def get(self, remote: str, local) -> None:  # pragma: no cover - never reached
        raise AssertionError("no guest read is expected in these tests")

    def run(self, command: str, *, timeout: int = 5400):  # pragma: no cover
        raise AssertionError("no guest command is expected in these tests")


def _governance() -> SimpleNamespace:
    return SimpleNamespace(
        environment_identity={"environment_id": "test-only"},
        evaluator=SimpleNamespace(
            evaluator_id="test-evaluator",
            version="0",
            implementation_digest="d" * 64,
            procedure_id="test-procedure",
        ),
    )


def _task_record(task_id: str) -> dict:
    return {
        "instance_id": task_id,
        "repo": "acme/widget",
        "base_commit": "a" * 40,
        "test_cmds": ["pytest -q tests/test_widget.py"],
        "FAIL_TO_PASS": ["tests/test_widget.py::test_widget"],
        "PASS_TO_PASS": [],
        "patch": "",
    }


def _pool(tmp_path, *, ids=(TASK, OTHER), excluded=(), anchor: str | None = None,
          content: str = CONTENT) -> tuple[dict, pathlib.Path]:
    pool = build_pool(
        task_ids=list(ids),
        registration_sha256=content,
        excluded=list(excluded),
        provenance={"registration_file_sha256": anchor or hashlib.sha256(REG_BYTES).hexdigest()},
    )
    path = tmp_path / "candidate_pool.json"
    path.write_bytes(canonical_bytes(pool))
    return pool, path


class World:
    """One fully synthetic v2 run, plus helpers to mutate exactly one thing."""

    def __init__(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        apply_repo_identity(monkeypatch)
        self.ledger_root = use_test_ledger(monkeypatch, tmp_path)
        # A directory the runner must never touch. The PRODUCTION root stays None.
        self.budget_root = tmp_path / "v2_budget_root"
        self.budget_root.mkdir()

        self.registration_file = tmp_path / "registered_tasks.json"
        self.registration_file.write_bytes(REG_BYTES)
        self.registration_provenance = tmp_path / "registered_tasks.provenance.json"
        self._write_provenance(hashlib.sha256(REG_BYTES).hexdigest())

        self.pool, self.pool_file = _pool(tmp_path)

        self.manifest_path = tmp_path / "manifest.json"
        self.manifest = {
            TASK: {
                "status": "built",
                "workspace": str(tmp_path / "ws"),
                "project_subdir": "",
            }
        }
        self.write_manifest()

        self.inputs_dir = tmp_path / "inputs"
        self.inputs_dir.mkdir()
        (self.inputs_dir / f"{TASK}.json").write_text(
            json.dumps(_task_record(TASK)), encoding="utf-8"
        )

        self.gates_path = tmp_path / "gates.json"
        self.gates_path.write_text(json.dumps(GATES), encoding="utf-8")

        self.out_dir = tmp_path / "out"
        self.staging_dir = tmp_path / "staging"

        self.priv, pub = keypair()
        self.trust_root = write_trust_root(tmp_path, {"owner": (self.priv, pub)})
        monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(self.trust_root))
        monkeypatch.setenv(
            "F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(self.trust_root)
        )

        self.record = signed_record(
            stage="S8-001",
            ops=("base_gold_production",),
            schema=RUNNER.V2_SCHEMA if hasattr(RUNNER, "V2_SCHEMA") else "f2-authorization/v2",
            candidate_manifest_digest=self.manifest_digest,
            registration_sha256=CONTENT,
            candidate_pool_digest=pool_digest(self.pool),
        )
        self.record_path = write_record(tmp_path, self.record, self.priv)
        monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(self.record_path))

    # -- helpers --------------------------------------------------------- #
    def _write_provenance(self, tasks_sha256: str, content: str = CONTENT) -> None:
        self.registration_provenance.write_text(
            json.dumps(
                {
                    "tasks_sha256": tasks_sha256,
                    "registration_content_sha256": content,
                }
            ),
            encoding="utf-8",
        )

    def add_input(self, task_id: str) -> None:
        (self.inputs_dir / f"{task_id}.json").write_text(
            json.dumps(_task_record(task_id)), encoding="utf-8"
        )

    def write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    @property
    def manifest_digest(self) -> str:
        return hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()

    def reissue(self, **overrides) -> None:
        """Re-sign the record with overrides (keeps the same trust root)."""
        record = dict(self.record)
        record.update(overrides)
        if "candidate_manifest_digest" not in overrides:
            record["candidate_manifest_digest"] = self.manifest_digest
        self.record = record
        self.record_path = write_record(self.tmp, record, self.priv)

    def rebuild_pool(self, **kwargs) -> None:
        _, self.pool_file = _pool(self.tmp, **kwargs)
        self.pool = json.loads(self.pool_file.read_text(encoding="utf-8"))
        self.reissue(candidate_pool_digest=pool_digest(self.pool))

    def argv(self, **overrides) -> list[str]:
        values = {
            "--manifest": str(self.manifest_path),
            "--inputs": str(self.inputs_dir),
            "--registration-id": "REG-TEST",
            "--registration-sha256": CONTENT,
            "--registration-file": str(self.registration_file),
            "--candidate-pool-file": str(self.pool_file),
            "--ledger": str(self.tmp / "factory_ledger.jsonl"),
            "--staging": str(self.staging_dir),
            "--out": str(self.out_dir),
            "--gates": str(self.gates_path),
        }
        values.update({k: v for k, v in overrides.items()})
        argv: list[str] = []
        for key, value in values.items():
            if value is None:
                continue
            argv.extend([key, str(value)])
        return argv


@pytest.fixture
def world(tmp_path, monkeypatch):
    assert BUDGET.PRODUCTION_BUDGET_ROOT is None, (
        "the production v2 budget root must stay unset for tests"
    )
    return World(tmp_path, monkeypatch)


@pytest.fixture
def order(monkeypatch) -> list[str]:
    """Record the REAL call order of grant verification, gate and consumption."""
    from qwen_train import f2_s8_v2_gate as G

    recorded: list[str] = []

    real_require = A.require_authorized

    def require(**kwargs):
        recorded.append("grant_verify")
        return real_require(**kwargs)

    real_consume = A.verify_and_consume

    def consume(**kwargs):
        recorded.append("consume")
        return real_consume(**kwargs)

    real_preflight = G.v2_preflight

    def preflight(**kwargs):
        recorded.append("preflight")
        return real_preflight(**kwargs)

    monkeypatch.setattr(A, "require_authorized", require)
    monkeypatch.setattr(A, "verify_and_consume", consume)
    monkeypatch.setattr(G, "v2_preflight", preflight)
    return recorded


# ---------------------------------------------------------------------------
# zero-side-effect assertions
# ---------------------------------------------------------------------------


def _snapshot(root: pathlib.Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class Recorder:
    """Counts every task invocation and returns a harmless synthetic result."""

    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.order = order

    def __call__(self, **kwargs) -> PilotTaskResult:
        spec = kwargs["spec"]
        self.calls.append(spec.task_id)
        if self.order is not None:
            self.order.append(f"task:{spec.task_id}")
        return PilotTaskResult(
            task_id=spec.task_id,
            admitted=True,
            outcome=ADMITTED_OUTCOME,
            outcome_class="scientific",
            failure_class=None,
            reason_code="",
            detail="test-only synthetic result",
        )


def _assert_refused(world: World, argv, expected_code: str, order, recorder):
    budget_before = _snapshot(world.budget_root)
    ledger_before = _snapshot(world.ledger_root)
    tmp_before = _snapshot(world.tmp)

    with pytest.raises(SystemExit) as caught:
        RUNNER.main(
            argv,
            task_runner=recorder,
            channel=FakeChannel(),
            governance_loader=_governance,
        )

    message = str(caught.value)
    assert f"code={expected_code}" in message, message
    assert recorder.calls == [], "a refused invocation executed a task"
    assert "consume" not in order, "a refused invocation consumed the grant"
    assert "task:" not in " ".join(order)

    # No consumption row, no reservation, no claim, no run artifact.
    assert AuthorizationLedger(world.ledger_root).find(
        record_digest=A.record_digest(world.record)
    ) is None
    assert not world.out_dir.exists(), "a refused invocation created the run directory"
    assert not world.staging_dir.exists()

    assert _snapshot(world.budget_root) == budget_before, "the budget root changed"
    assert _snapshot(world.ledger_root) == ledger_before, "the ledger changed"
    assert _snapshot(world.tmp) == tmp_before, "the working tree of the run changed"
    assert BUDGET.PRODUCTION_BUDGET_ROOT is None


def _run(world: World, argv, order, recorder=None):
    recorder = recorder or Recorder(order)
    return RUNNER.main(
        argv,
        task_runner=recorder,
        channel=FakeChannel(),
        governance_loader=_governance,
    ), recorder


# ---------------------------------------------------------------------------
# success ordering
# ---------------------------------------------------------------------------


def test_success_order_is_verify_gate_consume_then_task(world, order, monkeypatch):
    recorder = Recorder(order)

    # Count reads of the two gated artifacts from here on: the gate must read
    # each exactly once and the runner must never reopen either.
    real_read = pathlib.Path.read_bytes
    reads = {str(world.registration_file): 0, str(world.pool_file): 0}

    def counting(self):
        key = str(self)
        if key in reads:
            reads[key] += 1
        return real_read(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting)

    rc, recorder = _run(world, world.argv(), order, recorder)

    assert recorder.calls == [TASK], "the fake task runner was not reached exactly once"
    assert order[0] == "grant_verify"
    assert order.count("preflight") == 1
    assert order.count("consume") == 1
    assert order.index("preflight") < order.index("consume")
    assert order.index("consume") < order.index(f"task:{TASK}")

    # The consumption really happened, exactly once, for this grant.
    entry = AuthorizationLedger(world.ledger_root).find(
        record_digest=A.record_digest(world.record)
    )
    assert entry is not None
    assert entry["consumed_by_pid"] > 0

    # Read-once end to end: no reopen after the gate.
    assert reads[str(world.registration_file)] == 1
    assert reads[str(world.pool_file)] == 1

    # The report carries the gate's frozen evidence, not a re-read.
    payload = json.loads((world.out_dir / "pilot_results.json").read_text("utf-8"))
    evidence = payload["v2_preflight"]
    assert evidence["task_ids"] == [TASK]
    assert evidence["registration_file_sha256"] == hashlib.sha256(REG_BYTES).hexdigest()
    assert evidence["candidate_pool_digest"] == pool_digest(world.pool)
    assert payload["manifest_digest"] == world.manifest_digest
    # The post-batch audit needs real guest evidence, which a synthetic run has
    # none of: main() reports that honestly (1) instead of claiming acceptance.
    assert rc in (0, 1)


def test_second_run_with_the_same_grant_is_denied(world, order):
    _run(world, world.argv(), order)
    second = Recorder(order)
    budget_before = _snapshot(world.budget_root)
    with pytest.raises(SystemExit) as caught:
        RUNNER.main(
            world.argv(),
            task_runner=second,
            channel=FakeChannel(),
            governance_loader=_governance,
        )
    assert "code=AUTHORIZATION_REFUSED" in str(caught.value)
    assert second.calls == []
    assert _snapshot(world.budget_root) == budget_before


# ---------------------------------------------------------------------------
# gate refusals (all before consumption)
# ---------------------------------------------------------------------------



def test_unknown_schema_refused(world, order, monkeypatch):
    """Dispatch guard: a verified record naming no supported schema refuses."""
    monkeypatch.setattr(
        A, "require_authorized", lambda **kwargs: {"schema": "f2-authorization/v9"}
    )
    _assert_refused(world, world.argv(), "UNKNOWN_SCHEMA", order, Recorder(order))


def test_unsigned_grant_refused(world, order):
    payload = json.loads(world.record_path.read_text("utf-8"))
    payload["signature"]["sig"] = "A" * 88
    world.record_path.write_text(json.dumps(payload), encoding="utf-8")
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_grant_from_an_untrusted_signer_refused(world, order):
    other_priv, _ = keypair()
    world.record_path = write_record(world.tmp, world.record, other_priv, name="other.json")
    world.monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(world.record_path))
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_wrong_stage_refused(world, order):
    world.reissue(stage="EXPL-001")
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_wrong_operation_refused(world, order):
    world.reissue(operations=["acquire"])
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_revoked_grant_refused(world, order):
    trust = json.loads(world.trust_root.read_text("utf-8"))
    trust["revoked_record_ids"] = [world.record["id"]]
    world.trust_root.write_text(json.dumps(trust), encoding="utf-8")
    world.monkeypatch.setenv(
        "F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(world.trust_root)
    )
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_expired_grant_refused(world, order):
    past = now()
    world.reissue(
        not_before=(past.replace(year=past.year - 2)).isoformat(),
        not_after=(past.replace(year=past.year - 1)).isoformat(),
    )
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_not_yet_valid_grant_refused(world, order):
    future = now()
    world.reissue(
        not_before=(future.replace(year=future.year + 1)).isoformat(),
        not_after=(future.replace(year=future.year + 2)).isoformat(),
    )
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_malformed_timestamp_refused(world, order):
    world.reissue(not_before="not-a-date")
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_manifest_binding_mismatch_refused(world, order):
    world.reissue(candidate_manifest_digest="f" * 64)
    _assert_refused(world, world.argv(), "AUTHORIZATION_REFUSED", order, Recorder(order))


def test_missing_candidate_pool_argument_refused(world, order):
    _assert_refused(
        world, world.argv(**{"--candidate-pool-file": None}),
        "V2_MISSING_POOL_ARG", order, Recorder(order),
    )


def test_missing_registration_argument_refused(world, order):
    _assert_refused(
        world, world.argv(**{"--registration-file": None}),
        "V2_MISSING_REGISTRATION_ARG", order, Recorder(order),
    )


def test_missing_registration_file_refused(world, order):
    world.registration_file.unlink()
    _assert_refused(world, world.argv(), "MISSING_ARTIFACT", order, Recorder(order))


def test_missing_provenance_refused(world, order):
    world.registration_provenance.unlink()
    _assert_refused(world, world.argv(), "MISSING_ARTIFACT", order, Recorder(order))


def test_invalid_provenance_refused(world, order):
    world.registration_provenance.write_bytes(b"{not json")
    _assert_refused(world, world.argv(), "PROVENANCE_INVALID", order, Recorder(order))


def test_missing_pool_file_refused(world, order):
    world.pool_file.unlink()
    _assert_refused(world, world.argv(), "MISSING_ARTIFACT", order, Recorder(order))


def test_invalid_pool_schema_refused(world, order):
    world.pool_file.write_bytes(b'{"schema":"nope"}')
    _assert_refused(world, world.argv(), "POOL_INVALID", order, Recorder(order))


def test_pool_digest_mismatch_refused(world, order):
    pool = json.loads(world.pool_file.read_text("utf-8"))
    pool["task_ids"] = sorted(pool["task_ids"] + ["evil__x-9"])
    world.pool_file.write_bytes(canonical_bytes(pool))
    _assert_refused(world, world.argv(), "POOL_BYTES_CHANGED", order, Recorder(order))


def test_pool_registration_binding_mismatch_refused(world, order):
    world.rebuild_pool(content="e" * 64)
    _assert_refused(world, world.argv(), "POOL_BINDING_MISMATCH", order, Recorder(order))


def test_registration_bytes_modified_refused(world, order):
    world.registration_file.write_bytes(b'["acme__widget-1","acme__gadget-2","evil__x-9"]')
    _assert_refused(world, world.argv(), "REGISTRATION_BYTES_CHANGED", order, Recorder(order))


def test_grant_registration_digest_mismatch_refused(world, order):
    """A grant binding a DIFFERENT registration than the provenance declares.

    The CLI value is set to the same wrong digest so the runner's own binding
    check passes and the GATE is what refuses -- proving the gate, not the
    loader, is the layer that compares the signed content digest.
    """
    world.reissue(registration_sha256="d" * 64)
    _assert_refused(
        world,
        world.argv(**{"--registration-sha256": "d" * 64}),
        "PROVENANCE_DIGEST_MISMATCH",
        order,
        Recorder(order),
    )


def test_coordinated_registration_provenance_tamper_refused(world, order):
    """The attack the provenance chain alone could not see.

    Registration rewritten + ``tasks_sha256`` updated + content digest and grant
    untouched. Refused because the SIGNED pool also binds the registration's
    raw-byte digest.
    """
    tampered = b'["acme__widget-1","acme__gadget-2","evil__x-9"]'
    world.registration_file.write_bytes(tampered)
    world._write_provenance(hashlib.sha256(tampered).hexdigest())
    _assert_refused(world, world.argv(), "REGISTRATION_BYTES_CHANGED", order, Recorder(order))


def test_pool_with_a_different_registration_anchor_refused(world, order):
    world.rebuild_pool(anchor="0" * 64)
    # A different anchor is a bytes-changed refusal, not a missing one.
    _assert_refused(world, world.argv(), "REGISTRATION_BYTES_CHANGED", order, Recorder(order))


def test_out_of_pool_task_refused(world, order):
    world.add_input("evil__x-9")
    world.manifest = {"evil__x-9": dict(world.manifest[TASK])}
    world.write_manifest()
    world.reissue()
    _assert_refused(world, world.argv(), "OUT_OF_POOL", order, Recorder(order))


def test_excluded_task_refused(world, order):
    world.add_input(OTHER)
    world.rebuild_pool(
        ids=(TASK,),
        excluded=[{"task_id": OTHER, "reason": "terminal", "must_be_absent": True}],
    )
    world.manifest = {OTHER: dict(world.manifest[TASK])}
    world.write_manifest()
    world.reissue()
    _assert_refused(world, world.argv(), "EXCLUDED_TASK", order, Recorder(order))


def test_excluded_and_out_of_pool_prefers_the_exclusion(world, order):
    world.add_input(OTHER)
    world.rebuild_pool(
        ids=(TASK,),
        excluded=[{"task_id": OTHER, "reason": "terminal", "must_be_absent": True}],
    )
    world.manifest = {OTHER: dict(world.manifest[TASK])}
    world.write_manifest()
    world.reissue()
    _assert_refused(world, world.argv(), "EXCLUDED_TASK", order, Recorder(order))


def test_manifest_metadata_keys_are_not_treated_as_tasks(world, order):
    """``_``-prefixed keys and ``digest_policy`` are manifest metadata.

    A real manifest carries them; the gate must validate the runnable set, not
    the raw key set, or every real run would be refused as out-of-pool.
    """
    world.manifest["_meta"] = {"purpose": "test"}
    world.write_manifest()
    world.reissue()
    rc, recorder = _run(world, world.argv(), order)
    assert recorder.calls == [TASK]
    assert "preflight" in order



def test_credited_task_refused(world, order):
    AppendOnlyLedger(world.tmp / "factory_ledger.jsonl").append(
        {"task_id": TASK, "outcome": ADMITTED_OUTCOME, "outcome_class": "scientific"}
    )
    _assert_refused(world, world.argv(), "CREDITED_TASK", order, Recorder(order))


def test_terminal_task_refused(world, order):
    AppendOnlyLedger(world.tmp / "factory_ledger.jsonl").append(
        {"task_id": TASK, "outcome": "rejected", "outcome_class": "scientific"}
    )
    _assert_refused(world, world.argv(), "TERMINAL_TASK", order, Recorder(order))


def test_invalid_ledger_evidence_refused(world, order):
    (world.tmp / "factory_ledger.jsonl").write_text('{"seq": 9}\n', encoding="utf-8")
    _assert_refused(world, world.argv(), "LEDGER_INVALID", order, Recorder(order))


# ---------------------------------------------------------------------------
# v1 compatibility and v2 non-fallthrough
# ---------------------------------------------------------------------------


def _v1_world(world: World) -> None:
    world.reissue(schema="f2-authorization/v1")
    (world.tmp / "v1_marker").write_text("v1", encoding="utf-8")


def test_v1_grant_follows_the_legacy_path(world, order):
    """A supported v1 grant keeps its EXISTING behaviour: no v2 gate."""
    _v1_world(world)
    rc, recorder = _run(world, world.argv(**{"--candidate-pool-file": None,
                                             "--registration-file": None}), order)
    assert recorder.calls == [TASK]
    assert "preflight" not in order
    assert order.count("consume") == 1


def test_v2_grant_cannot_fall_through_to_the_v1_path(world, order):
    """Without the artifacts, a v2 grant refuses instead of running like v1."""
    _assert_refused(
        world,
        world.argv(**{"--candidate-pool-file": None, "--registration-file": None}),
        "V2_MISSING_POOL_ARG",
        order,
        Recorder(order),
    )


def test_v2_gate_failure_never_falls_back_to_v1(world, order):
    world.add_input("evil__x-9")
    world.manifest = {"evil__x-9": dict(world.manifest[TASK])}
    world.write_manifest()
    world.reissue()
    _assert_refused(world, world.argv(), "OUT_OF_POOL", order, Recorder(order))
    assert "preflight" in order
    assert "consume" not in order


def test_production_budget_root_is_none_and_fails_closed(world):
    """Slice A never reserves budget, and the production root stays unset."""
    assert BUDGET.PRODUCTION_BUDGET_ROOT is None
    with pytest.raises(Exception):
        BUDGET.budget_root(None)
    assert list(world.budget_root.iterdir()) == []


def test_production_defaults_do_not_carry_the_test_seam():
    params = inspect.signature(RUNNER.main).parameters
    for name in ("task_runner", "channel", "governance_loader"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is None
    argv_default = params["argv"].default
    assert argv_default is None
    # No CLI option exposes the seam.
    source = inspect.getsource(RUNNER.main)
    assert "--task-runner" not in source
    assert "--fake" not in source
